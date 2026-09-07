# Running Context

Companion to `docs/context/Gym-CONTEXT.md` — covers the running side of
training in detail. Like every file in `docs/context/`, this is a
human/design-reference document: nothing in the codebase reads it at
runtime. Its content is hand-ported into `main.py`'s `_ROBIN_SYSTEM`
(RUNNING KNOWLEDGE block) and `tools/gym.py`/`tools/running.py` — if this
doc changes, those need updating too for it to have any actual effect.

---

## Goals

| Goal | Current | Target |
|------|---------|--------|
| 5k time | 24:30 PB | Sub-20:00, by end of 2026 |
| Marathon time | 3:50 PB | 3:20, London Marathon, April 2027 |

---

## 2026 Philosophy — Base, VO2max, Resilience

2026 is deliberately **not** a marathon block. The focus is:

1. **Aerobic base** — building the low-intensity volume (cycling commute +
   easy running) that everything else sits on top of.
2. **VO2max / running-specific fitness** — getting comfortable running
   meaningfully faster than the 24:30 5k PB, via structured tempo/interval
   work.
3. **Resilience** — building the strength and connective-tissue tolerance
   (knees, lower back specifically) to absorb a real training load without
   breaking down, before that load actually arrives in marathon training.

Marathon-specific periodization (MP-pace long runs, taper, race-week
structure) is explicitly **out of scope for 2026**. Ollie will provide the
actual marathon training plan at the start of 2027, once base fitness is
established. Don't invent one before then.

**Training model:** polarized (80/20) rather than a moderate-intensity
grind — most volume easy, a small share hard, nothing in between.
Research consistently backs this for both performance and injury risk:
varying pace/load across the week reduces repetitive strain compared to
running every session at a similar "medium" effort, and it's the standard
approach for building a 5k/marathon base
([Marathon Handbook — polarized training](https://marathonhandbook.com/polarized-training/)).
In this plan, the cycling commute plus easy/long runs form the 80% easy
volume; the Sunday tempo/interval session is the 20% hard work.

---

## Weekly Structure

| Day | Activity |
|-----|----------|
| Mon | Cycle commute + gym (PPL slot 1 or 2) |
| Tue | Cycle commute only by default — no running scheduled, but fine on request (see "On-Demand Requests" below) |
| Wed | Cycle commute + gym (PPL slot 2 or 3) |
| Thu | Cycle commute only by default — no running scheduled, but fine on request |
| Fri | Cycle commute + gym (PPL slot, if due) |
| Sat | **Long/easy run** |
| Sun | **Tempo or interval run** |

- **2 dedicated running days to start** — Saturday (long/easy) and Sunday
  (tempo/interval). This can increase later in the year as resilience
  builds, but there's no fixed schedule for that yet — reassess by feel,
  don't force a 3rd day on a fixed date.
- **The gym's 3rd PPL slot alternates between legs and run** (see
  Gym-CONTEXT.md and `tools/gym.py:_next_leg_slot_type`) — so most weeks
  effectively get either 2 or 3 running-flavoured sessions depending on
  where the alternation lands.
- **Cycling commute (Mon–Fri) is untracked easy aerobic volume** — it
  counts toward the 80% base but isn't logged as a run.
- **Ride before you run** on any day both happen — cycling is
  concentric/non-impact and doesn't add eccentric loading before a run,
  whereas running first and cycling after adds fatigue to an already
  taxed system
  ([TrainingPeaks — cycling for runners](https://www.trainingpeaks.com/blog/using-your-bike-commute-as-training/)).
- **Competitive sport (golf, padel, tennis, football) is active recovery,
  not running training** — don't count it toward weekly running volume.

---

## On-Demand Requests & Missed-Session Recovery

The Sat/Sun + leg-slot-alternation structure above is the **default plan**,
not a hard constraint. In practice Ollie won't always land both weekend
runs, and should be able to just ask for a run recommendation on any day —
including Tue/Wed/Thu — and get a real answer, not a refusal based on the
calendar.

**When asked for a run on any day:**
- Always give a suggestion — call `get_session_plan(session_type="run")`
  for the menu of options (easy/long/tempo/interval/run-strength) and pick
  what fits, regardless of what day it is or where the PPL rotation
  currently sits.
- Check recent load first: `get_weekly_running_summary` (what's already
  been run this week) and recent gym history (`last_workout`/
  `open_session_today` in ambient context, or `get_weekly_gym_summary`) for
  whether a hard legs/run-strength session just happened.
- Use judgment, not a fixed rule. If load already looks high, or Ollie's
  mentioned soreness/tightness, it's right to push back — recommend an easy
  run instead of intervals, recommend run-strength/prehab instead of a run
  altogether, or recommend rest outright. Say why, don't just silently
  comply with whatever was asked for.

**Missed weekend run:** if the weekly summary shows Saturday's or Sunday's
run didn't happen, it's reasonable to proactively suggest making it up on
a Tue/Wed/Thu that week — framed as a suggestion ("didn't get Saturday's
long run in — Tuesday could work if legs feel fresh"), not an obligation,
and still subject to the same load-check/push-back judgment above (don't
suggest a catch-up run on top of a session that already looks like a lot).

---

## Training Paces

Rough VDOT-style paces derived from the 24:30 5k PB. These are **estimates
to adjust by feel**, not fixed targets — recalculate as fitness improves
through the year (a faster 5k time, or a race result, should trigger an
update here and in `_SESSION_PLANS["run"]` in `tools/gym.py`).

| Pace zone | Approx. pace | Use |
|-----------|--------------|-----|
| Easy | ~5:50–6:15/km | Aerobic base, long run, recovery jogs — conversational |
| Tempo | ~5:10/km | Comfortably hard, threshold work |
| Interval (VO2max) | ~4:35–4:40/km | 400–800m repeats, hardest structured effort |
| 5k race pace (current) | ~4:54/km | 24:30 PB pace |
| 5k race pace (target) | 4:00/km | Sub-20:00 goal — not a training pace yet, a destination |

---

## Injury Prevention & Run-Strength Progression

Ollie is specifically nervous about **knee and lower-back** injury as
running volume increases. Research backs building strength work in from
day one rather than after a niggle appears: strength training measurably
reduces overuse-injury rates in runners, and plyometric work should
progress conservatively rather than starting with loaded jumps
([TrainHeroic — strength training for runners](https://www.trainheroic.com/blog/strength-training-for-runners/)).

**Progression order** (earlier items first, add later items once the
earlier ones feel comfortable and pain-free — this is feel-based, not a
fixed number of weeks):

1. **Foundational, bodyweight** — basic hops, squat jumps (bodyweight
   only), glute bridges, bird-dogs, planks, side planks.
2. **Core/anti-rotation for the lower back** — Pallof press, dead bugs,
   suitcase carries — the core's job for running is resisting rotation
   over a single planted leg, not just crunches.
3. **Eccentric strength for the knees/hamstrings** — Nordic hamstring
   curls (one of the most effective injury-prevention exercises for
   runners), single-leg RDLs, calf raises.
4. **Loaded plyometrics** — jump squats with added load, box jumps,
   skater jumps — only once stages 1–3 are comfortable. Never introduce
   these on fatigued legs (e.g. straight after a hard leg session) —
   landing mechanics degrade under fatigue and that's when knee injuries
   happen.

This progression is what `_SESSION_PLANS["run"]`'s `"run-strength"` entry
in `tools/gym.py` should be pulling from when Robin suggests a
run-strength/prehab session for the alternating leg slot — lead with
stages 1–2 early on, introduce stage 4 only once it's clearly warranted.

---

## Marathon 2027 — Placeholder

Explicitly out of scope for 2026. Once Ollie provides the actual
periodization plan (expected ~January 2027), this section should be
replaced with the real block structure (base/build/peak/taper), long-run
progression, marathon-pace work, and race-week plan — following the same
pattern used by 16-week marathon plans generally: several weeks of
aerobic-base extension, several weeks of marathon-specific build (pace
work, longer long runs), a short peak, then a taper
([Marathon Training Academy — 3:20 plan](https://www.marathontrainingacademy.com/product/320-marathon-training-plan)).
Don't build this out speculatively before then.

---

*Last updated: September 2026*
