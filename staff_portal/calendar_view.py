"""A Monday-first annual calendar, built only from the signed-in person's sheets."""
import calendar
import datetime as dt
from collections import Counter


def annual_calendar(year, sheets, today):
    by_month = {sheet.month.month: sheet for sheet in sheets if sheet.month.year == year}
    months = []
    for number in range(1, 13):
        month = dt.date(year, number, 1)
        sheet = by_month.get(number)
        days_worked = Counter(entry.date.day for entry in sheet.entries.all()) if sheet else Counter()
        weeks = []
        for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, number):
            weeks.append([{
                "number": day,
                "today": bool(day and month.replace(day=day) == today),
                "shifts": days_worked[day],
            } for day in week])
        months.append({"month": month, "sheet": sheet, "weeks": weeks,
                       "is_current": month == today.replace(day=1)})
    return months
