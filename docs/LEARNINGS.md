# Learnings

## A value saved before it's final must be re-read once it is
- Context: FPL points/ranks showed 0 and last week's rank for GW3-7.
- Root cause: my_history was written once at the deadline (points still 0) and a "picks_synced" flag stopped it ever being refreshed; rivals had the same bug.
- Next time: when storing data that keeps changing after it's first available (scores, ranks, prices), store when it's *final* or refresh it then, and add a test that the later value replaces the early one.
- Seen: 2026-10-22 (bot/fpl_jobs._finalise_history, 1a58583)

## A breakdown's parts must each actually move the total
- Context: the league check blamed "bench -5" and "Attack +15" for a -15 gap.
- Root cause: bench points count for neither side, and a captain line of (points x2) double-counted a player both teams started.
- Next time: when splitting a difference into causes, check each cause changes the real total, count shared items once (cancelled out), and test that the parts sum to the whole on real data.
- Seen: 2026-10-22 (services/fpl_league.gw_review_decomposition, b568078)

## A failing external API isn't proof of a bug in our request
- Context: 18 meals logged at 0 kcal; logs showed USDA "HTTPStatusError".
- Root cause: USDA's search intermittently returns 400 to the exact same valid request; foods with no fallback value then logged as 0.
- Next time: when an external call fails, replay the identical request several times before changing it; for items used often, keep a curated fallback with sourced values.
- Seen: 2026-10-22 (services/nutrition._FALLBACK, 76d568f)
