"""Simple cron expression parser.

Supports: minute hour day_of_month month day_of_week
Special values: * (any), */N (every N), N (exact), N-M (range), N,M (list)
Presets: @hourly, @daily, @weekly, @monthly
"""
import time
from datetime import datetime, timezone, timedelta

PRESETS = {
    "@hourly": "0 * * * *",
    "@daily": "0 9 * * *",
    "@weekly": "0 9 * * 1",
    "@monthly": "0 9 1 * *",
    "@midnight": "0 0 * * *",
}


def _parse_field(field: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field into a set of valid values."""
    values = set()
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            values.update(range(min_val, max_val + 1))
        elif part.startswith("*/"):
            step = int(part[2:])
            values.update(range(min_val, max_val + 1, step))
        elif "-" in part:
            start, end = part.split("-")
            values.update(range(int(start), int(end) + 1))
        else:
            values.add(int(part))
    return values


def next_cron_time(expression: str, after: float = None) -> float:
    """Calculate the next run time (as unix timestamp) for a cron expression."""
    if expression in PRESETS:
        expression = PRESETS[expression]

    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expression}")

    minutes = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    days = _parse_field(parts[2], 1, 31)
    months = _parse_field(parts[3], 1, 12)
    weekdays = _parse_field(parts[4], 0, 6)  # 0=Monday in Python, but cron uses 0=Sunday
    # Convert cron weekdays (0=Sun) to Python weekdays (0=Mon)
    py_weekdays = set()
    for wd in weekdays:
        py_weekdays.add((wd - 1) % 7 if wd > 0 else 6)

    if after is None:
        after = time.time()

    # Start searching from next minute
    dt = datetime.fromtimestamp(after, tz=timezone.utc).replace(second=0, microsecond=0)
    dt += timedelta(minutes=1)

    # Search up to 1 year ahead
    max_iterations = 525960  # ~1 year in minutes
    for _ in range(max_iterations):
        if (
            dt.minute in minutes
            and dt.hour in hours
            and dt.day in days
            and dt.month in months
            and dt.weekday() in py_weekdays
        ):
            return dt.timestamp()
        dt += timedelta(minutes=1)

    raise ValueError(f"No next run time found for: {expression}")


def describe_cron(expression: str) -> str:
    """Human-readable description of a cron expression."""
    for name, expr in PRESETS.items():
        if expression == name or expression == expr:
            descriptions = {
                "@hourly": "Every hour at :00",
                "@daily": "Every day at 09:00 UTC",
                "@weekly": "Every Monday at 09:00 UTC",
                "@monthly": "1st of every month at 09:00 UTC",
                "@midnight": "Every day at 00:00 UTC",
            }
            return descriptions.get(name, name)

    parts = expression.strip().split()
    if len(parts) != 5:
        return expression

    return f"Cron: {expression} (min={parts[0]} hr={parts[1]} day={parts[2]} mon={parts[3]} dow={parts[4]})"
