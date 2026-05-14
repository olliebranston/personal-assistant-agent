# Personal Assistant Agent — Project Context

## Project overview
A Telegram bot that acts as a personal assistant. It receives messages,
understands intent, and responds using AI. Built in Python.

## Tech stack
- Language: Python
- Messaging layer: Telegram (python-telegram-bot library)
- AI model: OpenRouter API (free Gemini Flash model to start)
- Calendar: Google Calendar API (OAuth)
- Hosting: TBD (Railway or Render free tier)

## Bot capabilities (in build order)
1. Gym routine agent
2. Meal planning agent
3. Calendar organiser
4. News/sports agent
5. Scheduled morning briefing

## Gym preferences
- See Gym-CONTEXT.md

## Meal preferences
- See Mealplan-CONTEXT.md

## Calendar behaviour
- Provider: Google Calendar
- On receiving a forwarded message or plan details:
  ALWAYS confirm before creating an event. Format:
  "I'll add: [Event name], [Date], [Time], [Location if known] — shall I?"
- On querying: respond conversationally e.g. "You've got X at Y, then Z at W"

## News & sports interests
- Football: Chelsea FC (transfer rumours, match previews, results)
- Horse racing: [specific horses, trainers, or meetings you follow]
- Other sports: [e.g. F1, tennis, cricket — delete what doesn't apply]
- Other interests: [e.g. finance news, UK politics]
- Tone: concise, no fluff. Bullet points fine.

## Personality & tone
- Concise. No waffle.
- Confirm before taking actions (calendar events, anything irreversible)
- If something is ambiguous, ask one clarifying question
- Don't be sycophantic

## Developer notes
- Explain code as it's being written
- Build simplest version first, extend deliberately
- Flag shortcuts vs proper approaches