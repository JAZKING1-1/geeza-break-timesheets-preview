"""Finance downloads. The dashboard and exports use the same filtered records."""
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

import xlsxwriter
from django.utils import timezone

from .excel import submission_workbook


def finance_workbook(data):
    output = BytesIO()
    london = ZoneInfo("Europe/London")
    with xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False,
                                      "strings_to_urls": False}) as book:
        book.set_properties({"title": "Geeza Break finance report", "author": "Geeza Break"})
        title = book.add_format({"bold": True, "font_size": 20, "font_color": "#124B64"})
        heading = book.add_format({"bold": True, "bg_color": "#124B64", "font_color": "#FFFFFF", "text_wrap": True})
        text = book.add_format({"valign": "top", "text_wrap": True})
        number = book.add_format({"num_format": "0.00", "valign": "top"})
        summary = book.add_worksheet("Team summary")
        shifts = book.add_worksheet("All shifts")
        columns = [
            ["Employee", "Job title", "Month", "Status", "Worked minutes", "Recorded hours", "Approved hours", "Pending hours", "Reference"],
            ["Employee", "Month", "Status", "Work date", "Service / work type", "Start (UK)", "Finish (UK)", "Break minutes", "Worked minutes", "Decimal hours", "Purpose", "Reference"],
        ]
        generated = timezone.localtime().strftime("%d %b %Y %H:%M %Z")
        for page, headers in zip((summary, shifts), columns):
            page.hide_gridlines(2)
            page.freeze_panes(6, 1)
            page.set_landscape()
            page.fit_to_pages(1, 0)
            page.repeat_rows(5)
            page.set_column(0, len(headers) - 1, 22, text)
            page.merge_range(0, 0, 0, len(headers) - 1, f"GEEZA BREAK | {data['month']:%B %Y}", title)
            page.merge_range(2, 0, 2, len(headers) - 1, "Current records at download. Only Approved hours have been approved; this report does not confirm payment.", text)
            page.set_row(2, 32)
            employee = next((r["profile"].name for r in data["rows"]), "No matching employee") if data["employee_filter"] else "Everyone"
            status = data["status_filter"].replace("_", " ").title() or "All statuses"
            page.merge_range(3, 0, 3, len(headers) - 1, f"Employee: {employee} | Status: {status} | Downloaded: {generated}", text)
            page.set_row(3, 30)
            page.write_row(5, 0, headers, heading)
            page.set_row(5, 30)
        shift_row = 6
        for index, row in enumerate(data["rows"], start=6):
            sheet = row["sheet"]
            reference = f"TS-{sheet.pk} / r{sheet.revision}" if sheet else "No timesheet"
            hours = row["minutes"] / 60
            values = [row["profile"].name, row["profile"].job_title, data["month"].strftime("%Y-%m"),
                      row["label"], row["minutes"], hours,
                      hours if row["status"] == "approved" else 0,
                      hours if row["status"] != "approved" else 0, reference]
            summary.write_row(index, 0, values, text)
            for column in (5, 6, 7):
                summary.write_number(index, column, values[column], number)
            if not sheet:
                continue
            for entry in sheet.entries.all():
                shifts.write_row(shift_row, 0, [row["profile"].name, data["month"].strftime("%Y-%m"),
                    row["label"], entry.date.isoformat(), entry.category.name,
                    entry.starts_at.astimezone(london).strftime("%d %b %Y %H:%M %Z"),
                    entry.ends_at.astimezone(london).strftime("%d %b %Y %H:%M %Z"),
                    entry.break_minutes, entry.minutes, entry.minutes / 60, entry.notes, reference], text)
                shifts.write_number(shift_row, 9, entry.minutes / 60, number)
                shifts.set_row(shift_row, 36)
                shift_row += 1
        summary.autofilter(5, 0, 5 + len(data["rows"]), len(columns[0]) - 1)
        shifts.autofilter(5, 0, max(5, shift_row - 1), len(columns[1]) - 1)
        shifts.set_column(10, 10, 50, text)
        total_row = 7 + len(data["rows"])
        summary.write_string(total_row, 0, "TOTAL", heading)
        approved, pending = data["totals"]["approved"], data["totals"]["pending"]
        summary.write_number(total_row, 4, approved + pending)
        for column, minutes in ((5, approved + pending), (6, approved), (7, pending)):
            summary.write_number(total_row, column, minutes / 60, number)
    return f"geezabreak-{data['month']:%Y-%m}-team.xlsx", output.getvalue()


def timesheet_archive(data):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        name, content = finance_workbook(data)
        archive.writestr(name, content)
        for row in data["rows"]:
            if row["sheet"]:
                name, content = submission_workbook(row["sheet"], submitted_snapshot=False)
                archive.writestr(name, content)
        archive.writestr("READ-ME.txt", "Geeza Break timesheets\n\n"
            "This download follows the month, employee and status filters on the finance dashboard.\n"
            "The team workbook lists everyone matching those filters, including people with no timesheet.\n"
            "Individual files are included only where a timesheet exists. Status and revision are shown in each file.\n"
            "These are current records, not original submission snapshots. Draft, returned and submitted hours are not approved.\n"
            "Approval does not confirm payment. UK times and unpaid breaks are included.\n")
    return f"geezabreak-{data['month']:%Y-%m}-timesheets.zip", output.getvalue()
