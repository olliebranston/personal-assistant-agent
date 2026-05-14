"""Calendar agent. Handles event creation (with confirmation) and conversational event queries.

Calls: services.google_calendar
Rule: ALWAYS confirm before creating — format: "I'll add: [name], [date], [time], [location if known] — shall I?"
"""
