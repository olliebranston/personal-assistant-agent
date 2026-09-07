# Gym Context

## Goals

| Goal | Current | Target |
|------|---------|--------|
| 5k run time | 24:30 PB | Sub-20:00, by end of 2026 |
| Marathon time | 3:50 PB | 3:20, London Marathon April 2027 |
| Bench press | ~60–70kg | 100kg |
| Back squat | ~80–100kg | 150kg |
| Physique | Current weight of 105kg and 197 cm | Build muscle + lose fat (body recomp).Building muscle the priority |

See `docs/context/Running-CONTEXT.md` for the full running plan — 2026
philosophy, weekly structure, training paces, and injury-prevention
progression. This file covers the gym side only.


---

## Weekly Structure

Roughly 3 full gym sessions per week. On higher-activity weeks, up to 2 additional short sessions (<30 mins) — flexible, whatever fits that week.

**Typical week:**
- 3 × PPL sessions (45–60 mins each) — the 3rd slot alternates between a
  legs day and a run (see "Training Split" below and Running-CONTEXT.md)
- 5 × cycle to work, Mon–Fri (moderate cardio, not tracked)
- 2 × dedicated runs, Sat + Sun (see Running-CONTEXT.md)
- 2–4 × competitive sport per month (golf, padel, tennis, football — treat as active recovery / conditioning, not training)

No injuries. Ollie is cautious about knee and lower-back strain as running
volume increases — see the injury-prevention progression in
Running-CONTEXT.md before suggesting loaded plyometric work.

---

## Training Split — PPL (3-Day)

Running Push / Pull / Legs across 3 sessions. Each session 45–60 mins.

The 3rd ("legs") slot now alternates between an actual legs day and a run
— either a cardio run or a run-strength/prehab session — rather than
always being legs, per Ollie's goal of cutting leg-day frequency to make
room for running. This is enforced by `tools/gym.py:_next_leg_slot_type`,
not left to the LLM to infer: `get_next_session_type`/`get_session_plan`
already return whichever of "legs"/"run" is due. See Running-CONTEXT.md for
what a "run" 3rd-slot session should actually contain.

On a 5-day week, the two short sessions are unstructured — options listed below.

### Exercise Ordering Rule

Every session follows this order (enforced structurally by the ordered list
in `tools/gym.py:_SESSION_PLANS`, not left to the LLM to infer per session):

1. **Start with the session's signature compound/bodyweight movement** —
   push starts with dips, pull starts with pull-ups, legs starts with
   squats/Bulgarian split squats.
2. **Then the rest of the compound, multi-muscle movements**, most
   challenging/most-muscle-groups first (e.g. bench press, bent over bar
   rows, Romanian deadlifts, leg press) — before any isolation work.
3. **Isolation exercises last.** Within the isolation tail, group by
   equipment where more than one isolation exercise shares it (e.g. two
   dumbbell exercises back to back); when equipment doesn't group cleanly,
   order by muscle group, generally mirroring the muscle sequence the
   compounds already worked (e.g. push: chest → shoulders → triceps).

### Push — Chest, Shoulders, Triceps

**Fixed session order, exactly as `tools/gym.py:_SESSION_PLANS["push"]`
enforces it (this is what `get_session_plan` actually returns — see the
note under Exercise Library below for how the wider options here relate
to it):**
1. Dips — 4×10
2. Bench press — 5×8 (working weight: ~60–70kg, target: 100kg)
3. Overhead press — 4×8
4. One chest isolation exercise (rotate — pick from the list below) — 4×8
5. Dumbbell lateral raises — 4×15 ← natural 15+ rep set
6. Rope pulldowns — 4×8–10

**Chest isolation rotation (pick 1 for slot 4):**
- Pec fly machine — 4×8
- Cable pec fly — 3×10
- Incline dumbbell bench — 4×8
- Close-grip dumbbell bench — 4×8

**Other options — history/variety, not part of the fixed 6-slot rotation
above:**
- Chest press machine
- Cable/machine lateral raises — 3×10 (rotate with DB raises)
- Skullcrushers — 4×8
- Tricep extension (cable or machine) — 3×10 ← drop set candidate
- Ab finishers (2–3 exercises, tack on at end): ab crunches, sit-ups, plank

---

### Pull — Back, Biceps, Rear Delts

**Vertical pulls:**
- Pull-ups / chin-ups — 4×5–8
- Cable pulldowns — 4×8–10

**Horizontal rows:**
- Bent over bar rows — 5×10
- Single dumbbell rows — 4×10 *(pull towards hip, not chest)*
- Machine rows — 4×8 *(shoulder-width grip — keeps it a back exercise)*
- Cable rows — 4×8 *(same width cue)*
- T-bar rows — 3×8–10 (rotate in for variety)

**Rear delts (don't skip — shoulder health):**
- Face pulls — 4×10
- Cable delt fly — 4×8
- Machine delt fly — 4×10 (rotate with cable version)

**Biceps:**
- Bar bicep curls — 4×10
- Incline dumbbell curls — 4×10 *(long head stretch)*
- EZ bar curls — 4×8 (rotate with bar curls)
- Machine / dumbbell curls — 3×8–10 ← drop set candidate

**Back accessories (rotate in, not every session):**
- Shrugs — 3×10
- Upright rows — 4×10
- Dumbbell pullover — 3×10
- Front facing rows — 4×8

---

### Legs — Quads, Hamstrings, Glutes, Calves

**Primary compounds:**
- Squats (Smith machine) — 5×8 (working weight: ~80–100kg, target: 150kg)
- Bulgarian split squats — 4×10 *(do these early — they're brutal)*
- Leg press — 4×8
- Goblet squats — 4×10 (rotate in as higher-rep finisher)

**Hamstrings / posterior chain:**
- Romanian deadlifts — 4×10 *(hip hinge, bar close, soft knee)*
- Bent-over bell lifts — 5×8 *(brace hard)*
- Hamstring curls machine — 3×8
- Lunges — 3×10 each leg

**Isolation:**
- Quad extensions — 4×8–10
- Hip extensions / abductors — 4×10
- Calf raises — 4×15 ← natural 15+ rep set, slow eccentric

> **Time-tight legs session:** Squats + Bulgarians + RDLs + hamstring curls. That's the non-negotiable core.

---

## Short Sessions (<30 mins) — Flexible Options

No fixed prescription. Pick based on what was missed or what needs work:

- **Missed a muscle group?** Run a focused blast: 5–6 exercises, one area, minimal rest.
- **Cardio focus?** 20–25 min run (directly relevant to 5k goal — see below).
- **Full-body circuit?** Compound-only: bench / rows / squats / press, 3×8 each, move fast.
- **Weak point work?** Whatever's lagging — arms, rear delts, calves tend to get dropped from full sessions.

---

## Running — 5k & Marathon Progress

**Current 5k PB:** 24:30 (~4:54/km)
**5k target:** sub-20:00 (4:00/km), by end of 2026
**Marathon PB:** 3:50
**Marathon target:** 3:20, London Marathon April 2027

2026 is a base/VO2max/resilience-building year — no marathon-specific
periodization until Ollie provides that plan around January 2027. Full
detail (weekly structure, training paces, injury-prevention progression)
lives in `docs/context/Running-CONTEXT.md`; this section is a pointer, not
a duplicate.

Cycling to work gives a cardio base but doesn't directly train running
economy. Running sessions are now structured, not occasional: 2 dedicated
weekend runs (Sat long/easy, Sun tempo/interval) plus the PPL leg-slot
alternation described above.

Competitive sports count as active recovery, not running training. Don't conflate them.

---

## Key Principles

**Volume:**
- 10 sets/week per muscle = maximum growth signal
- 1–4 sets returns ~64% of the benefit — on a 3-day split you're in this range, which is fine
- If growth is a priority, running PPL twice (6 days) unlocks full volume — not realistic right now

**Rep ranges:**
- ¼ of sets should be 15+ reps
- Natural candidates: calf raises, lateral raises, cable flyes, rope pulldowns at higher reps
- Don't add exercises just to hit this — push reps on something you're already doing

**Drop sets:**
- Drop 20% → failure, drop another 20% → failure
- Fast to execute, still triggers growth
- Best on isolation finishers: tricep extensions, machine curls, pec fly, rope pulldowns
- Don't use on heavy compounds — fatigue cost too high

**Form cues (from training logs):**
- Diagonal/twist grip on bars: rotate bar to diagonal in the palm so force travels down the forearm
- Machine rows + cable rows: shoulder-width grip to keep it a back exercise
- Dumbbell rows: pull towards hip, not chest — better lat recruitment
- Form before weight, always

**Progressive overload:**
- This is the most important variable for both strength goals (bench 100kg, squat 150kg)
- The routine is the easy part; the tracking is where the actual gains come from
- The actual rule Robin applies (`tools/gym.py:_PROGRESSION_CYCLE`/
  `_PROGRESSION_INCREMENT_KG`): sets×reps advance through a fixed 4-step
  cycle at a given weight — 3×8 → 3×10 → 4×8 → 4×10. Completing 4×10 bumps
  the weight by +2.5kg and resets to 3×8 at the new weight. The
  recommendation is always based on the most advanced (weight, cycle-step)
  ever logged for an exercise, not just the last session, so one off day
  doesn't drag the target backwards — see `get_exercise_progression`.

---

## Exercise Library (Full List by Muscle)

All exercises logged across training sessions, plus rotation candidates.
**This is history/variety material, not what `get_session_plan` returns** —
the tool encodes a fixed 6-exercise rotation per session type
(`tools/gym.py:_SESSION_PLANS`, per the Push/Pull/Legs sections above); this
library is the broader set an exercise might be logged under or rotated
into, not a literal menu the bot picks from live.

### Chest
Close grip dumbbell bench, incline dumbbell bench, chest press machine, bench press, cable pec fly, pec fly machine, dips (forward lean), diamond press-ups

### Shoulders
Overhead press, military press, shoulder press machine, converging shoulder press, axle press, dumbbell overhead press, dumbbell lateral raises, cable lateral raises, machine lateral raises, upright row, raises (light, 5kg)

### Triceps
Rope pulldowns, rope tricep extension, tricep press machine, tricep extension machine, skullcrushers, dumbbell tricep extension, diamond press-ups

### Back
Bent over bar rows, bent over dumbbell rows, single dumbbell rows, machine rows, cable rows, front facing rows, T-bar rows, cable pulldowns (machine + cable), pull-ups, chin-ups, dumbbell pullover, shrugs, upright row

### Biceps
Bar bicep curls, machine bicep curls, dumbbell bicep curls, EZ bar curls, incline dumbbell curls, rope pull-ups

### Rear Delts
Face pulls, cable delt fly, machine delt fly

### Legs
Squats (Smith machine), Bulgarian split squats, lunges, goblet squats, leg press, Romanian deadlifts, bent-over bell lifts, hamstring curls, quad extensions, hip extensions, calf raises

### Core
Ab crunches, sit-ups, plank, crunches

---

## Weight Notation (from logs)

- `s[X]` = starting weight for that set (warm-up or first working set)
- Main number = working weight in kg
- Example: `s35 70 5×8` = start at 35kg, working sets at 70kg, 5 sets of 8 reps
- Where lbs noted, convert: 75 lbs ≈ 34kg

---

*Last updated: September 2026*