"""Scheduled jobs registered with python-telegram-bot's JobQueue (backed by APScheduler).

Jobs to implement:
- morning_briefing     — daily, configurable time
- meal_midmorning      — weekday snack/protein reminder if behind target
- meal_evening         — evening dinner check-in
- meal_end_of_day      — daily macro summary
- meal_friday_list     — Friday shopping list + week summary
- gym_batch_cook       — Sunday batch-cook prompt
"""
