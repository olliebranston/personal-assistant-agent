#!/usr/bin/env python3
"""
fpl_squad_v0.py — Phase-2 prototype: xP heuristic + MILP squad optimiser.

This is the v0 model described in FPL-CONTEXT.md §4.2: no Dixon-Coles, no
Poisson, just last-season points-per-game adjusted for fixtures, minutes
reliability and availability. It is deliberately crude. Its job is to be
*legal, fast and honest*, not clever.

The important part is the MILP: every squad it emits is guaranteed valid
(budget, 2/5/5/3, max 3 per club, legal formation). That is the "hallucination
firewall" — the LLM never picks players, the solver does.

Usage (from the repo root — reads teams.csv/fixtures.csv/players_raw.csv
from the current working directory, not this file's location):
    python3 services/fpl_squad_v0.py                       # unconstrained optimum
    python3 services/fpl_squad_v0.py --force Haaland Palmer --min-club CHE=2
"""
import argparse
import collections
import csv
import sys
import pulp

POS = {'1': 'GK', '2': 'DEF', '3': 'MID', '4': 'FWD'}
SQUAD_LIMITS = {'GK': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GK': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GK': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}


def num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load(gw_horizon=6):
    teams = {r['id']: r['short_name'] for r in csv.DictReader(open('teams.csv'))}

    # Average fixture difficulty over the horizon, per team.
    diff = collections.defaultdict(list)
    for r in csv.DictReader(open('fixtures.csv')):
        if not r['event']:
            continue
        if int(float(r['event'])) > gw_horizon:
            continue
        diff[teams[r['team_h']]].append(int(r['team_h_difficulty']))
        diff[teams[r['team_a']]].append(int(r['team_a_difficulty']))
    fdr = {t: sum(v) / len(v) for t, v in diff.items()}

    players = []
    for r in csv.DictReader(open('players_raw.csv')):
        team = teams[r['team']]
        ppg = num(r['points_per_game'])
        mins = num(r['minutes'])

        # Availability. status: a=available, d=doubtful, i=injured,
        # s=suspended, u=unavailable, n=on loan / not in squad.
        chance = r['chance_of_playing_next_round']
        if r['status'] == 'a':
            avail = 1.0
        elif chance not in ('', 'None', None):
            avail = num(chance) / 100.0
        elif r['status'] == 'd':
            avail = 0.5
        else:
            avail = 0.0

        # How much do we trust last season's ppg? A player with 300 minutes
        # is a coin flip; one with 3000 is a real sample.
        reliability = 0.55 + 0.45 * min(1.0, mins / 2500.0)

        # Easier fixtures than average (3.0) scale points up, harder scale down.
        fixture = 1.0 + (3.0 - fdr.get(team, 3.0)) * 0.10

        xp = ppg * reliability * fixture * avail

        players.append({
            'id': r['id'], 'name': r['web_name'], 'team': team,
            'pos': POS[r['element_type']], 'cost': int(r['now_cost']),
            'own': num(r['selected_by_percent']), 'ppg': ppg, 'mins': mins,
            'xgi90': num(r['expected_goal_involvements_per_90']),
            'dc90': num(r['defensive_contribution_per_90']),
            'fdr': fdr.get(team, 3.0), 'avail': avail, 'xp': xp,
            'news': r['news'],
        })
    return players


def solve(players, budget=1000, force=(), ban=(), min_club=None, bench_weight=0.15,
          min_template=0, template_threshold=15.0):
    """MILP: choose 15, choose XI, choose captain. Every constraint here is
    hard, which is what makes the output trustworthy."""
    min_club = min_club or {}
    prob = pulp.LpProblem('fpl_squad', pulp.LpMaximize)
    idx = range(len(players))
    sq = pulp.LpVariable.dicts('squad', idx, cat='Binary')   # in the 15
    st = pulp.LpVariable.dicts('start', idx, cat='Binary')   # in the XI
    cp = pulp.LpVariable.dicts('capt', idx, cat='Binary')    # captain

    # Objective: XI points + captain's points again + a little credit for the
    # bench (a bench that never plays is still insurance).
    prob += pulp.lpSum(
        players[i]['xp'] * (st[i] + cp[i] + bench_weight * (sq[i] - st[i]))
        for i in idx
    )

    prob += pulp.lpSum(sq[i] for i in idx) == 15
    prob += pulp.lpSum(st[i] for i in idx) == 11
    prob += pulp.lpSum(cp[i] for i in idx) == 1
    prob += pulp.lpSum(players[i]['cost'] * sq[i] for i in idx) <= budget

    for i in idx:
        prob += st[i] <= sq[i]     # can only start someone you own
        prob += cp[i] <= st[i]     # can only captain someone who starts

    for p, n in SQUAD_LIMITS.items():
        prob += pulp.lpSum(sq[i] for i in idx if players[i]['pos'] == p) == n
    for p in XI_MIN:
        prob += pulp.lpSum(st[i] for i in idx if players[i]['pos'] == p) >= XI_MIN[p]
        prob += pulp.lpSum(st[i] for i in idx if players[i]['pos'] == p) <= XI_MAX[p]

    for t in {p['team'] for p in players}:
        prob += pulp.lpSum(sq[i] for i in idx if players[i]['team'] == t) <= 3

    # Names collide (there are two 'Palmer's). Accept 'Name' or 'Name@TEAM'
    # and refuse to guess when a bare name is ambiguous — guessing here is
    # exactly how an agent ends up recommending the wrong player.
    def match(spec):
        if '@' in spec:
            n, t = spec.split('@')
            return [i for i in idx if players[i]['name'] == n and players[i]['team'] == t]
        hits = [i for i in idx if players[i]['name'] == spec]
        if len(hits) > 1:
            opts = ', '.join(f"{players[i]['name']}@{players[i]['team']}" for i in hits)
            sys.exit(f"ambiguous name '{spec}' — did you mean: {opts}")
        return hits

    for spec in force:
        hits = match(spec)
        if not hits:
            sys.exit(f"forced player not found: {spec}")
        prob += pulp.lpSum(sq[i] for i in hits) == 1
    for spec in ban:
        for i in match(spec):
            prob += sq[i] == 0
    for club, n in min_club.items():
        prob += pulp.lpSum(sq[i] for i in idx if players[i]['team'] == club) >= n

    # Template shield: own at least N of the widely-held players. Not owning a
    # popular player is an asymmetric risk — if he hauls you lose ground to the
    # whole league; if he blanks you gain nothing, because everyone else also
    # took the zero.
    if min_template:
        prob += pulp.lpSum(st[i] for i in idx
                           if players[i]['own'] >= template_threshold) >= min_template

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != 'Optimal':
        sys.exit(f"solver status: {pulp.LpStatus[prob.status]}")

    squad = [players[i] for i in idx if sq[i].value() > 0.5]
    xi = {players[i]['id'] for i in idx if st[i].value() > 0.5}
    capt = [players[i]['name'] for i in idx if cp[i].value() > 0.5][0]
    return squad, xi, capt, pulp.value(prob.objective)


def show(squad, xi, capt, obj, label):
    order = {'GK': 0, 'DEF': 1, 'MID': 2, 'FWD': 3}
    squad.sort(key=lambda p: (order[p['pos']], -p['cost']))
    print(f"\n{'='*84}\n{label}\n{'='*84}")
    print(f"{'':2}{'name':<16}{'tm':<5}{'pos':<5}{'£':>5}{'own%':>7}{'ppg':>6}{'xGI90':>7}{'DC90':>7}{'fdr6':>6}{'xP':>6}")
    for p in squad:
        mark = 'C ' if p['name'] == capt else ('  ' if p['id'] in xi else '~ ')
        print(f"{mark}{p['name']:<16}{p['team']:<5}{p['pos']:<5}{p['cost']/10:>5.1f}{p['own']:>7.1f}"
              f"{p['ppg']:>6.1f}{p['xgi90']:>7.2f}{p['dc90']:>7.2f}{p['fdr']:>6.2f}{p['xp']:>6.2f}")
    cost = sum(p['cost'] for p in squad)
    tmpl = sum(p['own'] for p in squad if p['id'] in xi)
    print(f"\n  cost £{cost/10:.1f}m   bank £{(1000-cost)/10:.1f}m   captain {capt}"
          f"   objective {obj:.2f}   XI ownership sum {tmpl:.0f}%   (~ = bench)")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', nargs='*', default=[])
    ap.add_argument('--ban', nargs='*', default=[])
    ap.add_argument('--min-club', nargs='*', default=[], help='e.g. CHE=2')
    ap.add_argument('--horizon', type=int, default=6)
    ap.add_argument('--min-template', type=int, default=0,
                    help='require at least N players owned by >=15%% of the field')
    ap.add_argument('--min-bank', type=int, default=0, help='reserve N tenths of a million')
    ap.add_argument('--min-minutes', type=float, default=900,
                    help='exclude players with fewer minutes last season')
    args = ap.parse_args()

    mc = dict(kv.split('=') for kv in args.min_club)
    mc = {k: int(v) for k, v in mc.items()}

    players = load(args.horizon)
    keep = [p for p in players
            if p['avail'] > 0.74 and (p['mins'] >= args.min_minutes or p['name'] in args.force)]
    print(f"{len(players)} players loaded, {len(keep)} pass availability/minutes filters")

    squad, xi, capt, obj = solve(keep, budget=1000 - args.min_bank, force=args.force,
                                 ban=args.ban, min_club=mc, min_template=args.min_template)
    show(squad, xi, capt, obj, 'OPTIMAL SQUAD')