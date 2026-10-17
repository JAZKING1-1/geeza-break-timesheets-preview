"""Monthly payroll dates, calculated in the portal's UK local calendar."""
import calendar
import datetime as dt

from django.utils import timezone


def payroll_dates(month, today=None):
    today = today if today is not None else timezone.localdate()
    last_day = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    payday = last_day - dt.timedelta(days=(last_day.weekday() - 3) % 7)
    deadline = payday - dt.timedelta(days=14)
    days_left = (deadline - today).days
    return {
        "month": month.replace(day=1), "payday": payday, "deadline": deadline,
        "days_left": days_left, "overdue": days_left < 0, "due_today": days_left == 0,
    }
