# Robin — User Guide

Robin is your personal assistant on Telegram. Just talk to it naturally — no commands to memorise, no menus. It figures out what you need from context.

---

## What Robin does

- **Tracks your training** — suggests sessions with personalised targets, logs lifts, tracks running progress toward your 5k goal
- **Logs food and macros** — USDA-verified protein and kcal, daily and weekly summaries, correction flow for wrong values
- **Plans your meals** — weekly meal plan, recipe cards on demand, derived shopping list every Friday
- **Manages your calendar** — reads and writes Google Calendar events with confirmation
- **Sends you news** — Chelsea FC, world headlines, your horses (Racing API), today's calendar
- **Sets reminders** — one-off alerts at any time you specify
- **Proactively checks in** — morning briefing, mid-morning protein nudge, dinner prompt, end-of-day summary, Friday shopping list, Sunday batch cook recipe

---

## Daily schedule (what Robin sends without being asked)

| Time | What you get |
|---|---|
| **7:45am** | Morning brief: today's calendar events, training targets for every exercise, any horses running, Chelsea headline if fresh, breakfast suggestion |
| **10:30am (weekdays)** | Protein nudge if you've logged under 60g by that point — skips silently if you're on track |
| **12:30pm (Tue/Wed/Thu)** | Lunch prompt showing yesterday's lunch — say "same lunch" to log it in one message |
| **9:00pm** | Dinner prompt — reply with what you had and it's logged |
| **11:00pm** | End-of-day summary: protein vs 230g target, kcal vs target, any flags |
| **Friday 5pm** | Week macro summary + next week's meal plan + full shopping list |
| **Sunday 10am** | Batch cook recipe card with ingredients, method and order of operations |

---

## Training

### Getting a session plan

Just ask. Robin checks your last session of each type and gives targets for every exercise you logged, not just the compounds.

```
what's my next session?
next workout
```

Override the automatic cycle if you want a specific day:

```
give me push day
switch to legs
I want to do pull
short session today
going for a run
```

The session plan shows:
- What you did last time (every exercise, weight, sets×reps)
- Today's targets (+2.5kg or +1 rep per exercise)
- The full session plan

### Logging a session

After your session, send your lifts in any natural format:

```
bench 80kg 5×5, OHP 52.5kg 4×8, rope pulldowns 40kg 4×10, dips BW 4×10
```

Robin infers the session type (push/pull/legs) from the exercises. If you hit less than 100g protein so far that day, it'll flag the post-workout window for a shake.

**Logging a run:**
```
ran 5k in 26:30
did a 3k easy run
ran 8k, about 5:30/km pace
```

### Checking history

```
bench history
squat last
how's my running?
5k progress
how did I do this week
```

Running history shows your last 6 runs with the gap to your 20:00 5k goal.

---

## Food logging

### Logging what you ate

Just describe it naturally. Robin looks up macros via USDA and logs immediately if the match is confident. It always shows what it assigned before moving on.

```
had 200g Greek yoghurt, 80g oats and 50g dates for breakfast
protein smoothie with 2 scoops whey, berries and peanut butter
lunch was the red lentil dal
pint of Guinness and two glasses of wine
```

**What you'll see after logging:**
```
Logged:
  200g Greek yoghurt — 20g protein, 118 kcal
  80g oats — 14g protein, 311 kcal
  50g dates — 1g protein, 141 kcal

Total: 35g protein, 570 kcal
Today: 85g protein / 1,240 kcal (target: 2,950 kcal)
145g protein to go — pre-bed shake covers most of it.
Wrong? Say 'correct it' or e.g. 'change the oats to 20g protein'.
```

If anything looks off (wrong USDA match, wrong portion), correct it immediately.

### Correcting a logged entry

Three ways to correct, all work naturally:

```
actually that was 300g Greek yoghurt
change the chicken to 62g protein
add 15g protein to that
the oats should be 250g not 80g
```

Robin finds the most recent matching entry and updates it, then shows your revised running total.

### Quick repeat on Tue/Wed/Thu

If you had the same breakfast or lunch as yesterday, Robin will show it in the morning briefing and lunch prompt:

> "Same as yesterday? 200g Greek yoghurt, 80g oats (42g protein). Say 'same breakfast' to log it."

Just reply:
```
same breakfast
same lunch
```

### Checking your numbers

```
what's my protein today
summary
how much left
what did I eat yesterday
how did I do this week
```

**Weekly summary** shows average protein, average kcal, and flags any low-protein days (<75% of target).

### Logging your weight

Mention it naturally, any time:

```
I weighed 104.2kg this morning
104.5 today
weighed 103.8
how's my weight going?
```

Robin logs it and shows the recent trend.

---

## Meal planning and recipes

### Getting a recipe

Ask for any meal Robin knows:

```
give me the recipe for miso salmon
how do I make pad thai
ingredients for red lentil dal
recipe for tofu katsu curry
```

Robin has 23 recipes covering all the weekday dinners, weekend dinners, batch cook lunches and weekend breakfasts.

### Getting a meal suggestion

```
suggest dinner
what should I have for breakfast
suggest a snack
what's for lunch
```

### Planning your week

```
plan my week
what am I cooking this week
generate a meal plan
```

Robin picks this week's batch cook rotation (4 portions, Mon–Thu lunches) and 4 dinners, then generates a full shopping list grouped by:
- **PROTEIN & FISH** — salmon, tofu, prawns, eggs etc.
- **VEG & FRESH** — whatever the recipes need
- **DAIRY** — yoghurt, feta, butter etc.
- **STORE CUPBOARD** — top-ups for non-staple items

This also fires automatically every **Friday at 5pm** with your week's nutrition summary.

**Swapping a meal in the plan:**
```
swap Friday dinner for tempeh rendang
change Saturday to shakshuka
```

---

## Calendar

### Checking your schedule

```
what have I got on today?
what's on this week?
anything on tomorrow?
what's on next weekend?
```

### Adding an event

Describe it naturally or forward a message — Robin confirms before creating anything:

```
add dentist Friday 10am
dinner at The Anchor Tuesday 7pm
golf Saturday morning
```

Robin will reply: *"I'll add: Dentist, Friday 13 Jun, 10:00am — shall I?"*

Reply **yes** to confirm, or describe what to change.

**Forwarded messages work too** — if someone sends you event details, forward the message to Robin and it'll parse the date, time and location.

---

## News

```
news
what's the news
/news
```

Returns four sections:
1. **Chelsea FC** — transfers, signings, injuries, contract news (last 48h from BBC Sport)
2. **World** — top geopolitical stories (last 24h from BBC World)
3. **Your horses** — today and tomorrow's entries from the Racing API (course, off time, distance, going, form)
4. **Today's calendar** — a one-liner of what you've got on

---

## Reminders

```
remind me at 3pm to call the dentist
message me tomorrow at 9am about the rent
alert me in 2 hours
remind me Thursday morning to check the bookings
```

Robin confirms: *"Reminder set for today at 15:00 — 'call the dentist'."*

Reminders are in-memory — they won't survive a bot restart, but the bot rarely restarts.

---

## Tips

**Robin remembers context within a conversation** — you don't need to repeat yourself for follow-ups. If your last exchange was about gym, a short follow-up like "what about legs?" will be understood.

**Alcohol is logged without moralising** — just describe it:
```
two pints of Guinness and a glass of red
three pints of lager
```

**Large portions are assumed** — Robin defaults to large portions for a 105kg active male. A "chicken breast" = 200g, a "bowl of rice" = 220g cooked. If you had something smaller, say so:
```
had a small chicken breast, maybe 150g
half portion of rice
```

**USDA sometimes mismatches** — less common foods, branded products, or vague descriptions occasionally pull the wrong entry. If the protein looks wrong, correct it immediately with "change the [food] to Xg protein" before logging anything else.

**The correction flow works best on the same day** — Robin always targets the most recent matching entry for today. If you need to correct something from yesterday, be specific: "change yesterday's chicken to 62g protein".

---

## What Robin doesn't do (yet)

- **Historical race results** — the Racing API free tier only covers today and tomorrow's entries. Past results need a Pro plan upgrade.
- **Persistent reminders** — reminders are lost if the bot restarts. Use Google Calendar for anything critical.
- **Multiple users** — Robin is configured for one Telegram user ID only.
- **Photo logging** — meal photos aren't processed. Describe food in text.

---

## Quick reference

| What you want | Say |
|---|---|
| Next gym session | "what's my next session" |
| Specific session | "give me push day" / "switch to legs" |
| Log a run | "ran 5k in 26:30" |
| Running progress | "how's my running" |
| Log food | just describe what you ate |
| Fix a wrong entry | "change the chicken to 62g protein" |
| Repeat yesterday's meal | "same breakfast" / "same lunch" (Tue–Thu) |
| Today's macros | "what's my protein today" / "summary" |
| Macros remaining | "how much left" |
| Yesterday's food | "what did I eat yesterday" |
| Weekly nutrition | "how did I do this week" |
| Log weight | "I weighed 104.2kg" |
| Weight trend | "how's my weight going" |
| Recipe | "recipe for miso salmon" |
| Meal suggestion | "suggest dinner" |
| Week plan + shopping list | "plan my week" |
| Calendar today | "what have I got on today" |
| Add event | "dentist Friday 10am" |
| News | "news" or /news |
| Reminder | "remind me at 3pm to call X" |
