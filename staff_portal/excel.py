"""Excel copies of submitted hours, generated entirely on the application server."""
import datetime as dt
from io import BytesIO
from zoneinfo import ZoneInfo

import xlsxwriter
from django.utils.text import slugify

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def submission_workbook(sheet):
    """Return a named XLSX snapshot; never interpret staff input as formulas or links."""
    entries = list(sheet.entries.select_related("category").order_by("date", "start_time", "pk"))
    total_minutes = sum(entry.minutes for entry in entries)
    output = BytesIO()
    london = ZoneInfo("Europe/London")
    with xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False,
                                      "strings_to_urls": False}) as book:
        book.set_properties({"title": "Geeza Break submitted timesheet", "author": "Geeza Break"})
        page = book.add_worksheet("Submitted timesheet")
        page.hide_gridlines(2)
        page.freeze_panes(10, 2)
        page.set_landscape()
        page.set_paper(9)
        page.fit_to_pages(1, 0)
        page.repeat_rows(9)
        page.set_footer("Geeza Break | Submitted claim | Page &P of &N")
        page.set_column("A:A", 14)
        page.set_column("B:B", 24)
        page.set_column("C:D", 26)
        page.set_column("E:F", 15)
        page.set_column("G:H", 16)
        page.set_column("I:I", 55)

        title = book.add_format({"bold": True, "font_size": 20, "font_color": "#124B64"})
        label = book.add_format({"bold": True, "font_color": "#124B64", "font_size": 12})
        text = book.add_format({"font_size": 12, "valign": "top", "text_wrap": True})
        banner = book.add_format({"font_size": 12, "text_wrap": True, "bg_color": "#E6F5F7"})
        date = book.add_format({"font_size": 12, "num_format": "dd mmm yyyy", "valign": "top"})
        integer = book.add_format({"font_size": 12, "num_format": "0", "valign": "top"})
        duration = book.add_format({"font_size": 12, "num_format": "[h]:mm", "valign": "top"})
        decimal = book.add_format({"font_size": 12, "num_format": "0.00", "valign": "top"})
        heading = book.add_format({"font_size": 12, "bold": True, "font_color": "#FFFFFF",
                                   "bg_color": "#124B64", "text_wrap": True, "valign": "vcenter"})

        page.merge_range("A1:I1", "GEEZA BREAK | Submitted timesheet", title)
        page.set_row(0, 32)
        for row, name, value in [(2, "Employee", sheet.staff.name),
                                 (3, "Role", sheet.staff.job_title),
                                 (4, "Work email", sheet.staff.email or "Not recorded")]:
            page.write_string(row, 0, name, label)
            page.merge_range(row, 1, row, 3, value, text)
        page.write_string("F3", "Claim month", label)
        page.merge_range("G3:I3", sheet.month.strftime("%B %Y"), text)
        page.write_string("F4", "Submitted", label)
        submitted = sheet.submitted_at.astimezone(london).strftime("%d %b %Y %H:%M %Z")
        page.merge_range("G4:I4", submitted, text)
        page.write_string("F5", "Reference", label)
        page.merge_range("G5:I5", f"TS-{sheet.pk} / revision {sheet.revision}", text)
        page.merge_range("A7:I7", "Submitted for review. This copy is not approval or confirmation of payment.", banner)
        page.set_row(6, 25)
        page.merge_range("A8:I8", "UK local times (BST/GMT). Worked minutes include clock changes and exclude unpaid breaks. Decimal hours are rounded for display.", text)
        page.set_row(7, 32)

        headers = ["Date worked", "Service / work type", "Start (UK)", "Finish (UK)",
                   "Break (minutes)", "Worked (minutes)", "Hours (h:mm)", "Decimal hours", "Purpose"]
        for column, value in enumerate(headers):
            page.write_string(9, column, value, heading)
        page.set_row(9, 36)
        for row, entry in enumerate(entries, start=10):
            page.write_datetime(row, 0, dt.datetime.combine(entry.date, dt.time()), date)
            page.write_string(row, 1, entry.category.name, text)
            page.write_string(row, 2, entry.starts_at.astimezone(london).strftime("%d %b %Y %H:%M %Z"), text)
            page.write_string(row, 3, entry.ends_at.astimezone(london).strftime("%d %b %Y %H:%M %Z"), text)
            page.write_number(row, 4, entry.break_minutes, integer)
            page.write_number(row, 5, entry.minutes, integer)
            page.write_number(row, 6, entry.minutes / 1440, duration)
            page.write_number(row, 7, entry.minutes / 60, decimal)
            page.write_string(row, 8, entry.notes, text)
            page.set_row(row, max(36, 17 * (2 + len(entry.notes) // 48 + entry.notes.count("\n"))))
        last = 9 + len(entries)
        page.autofilter(9, 0, last, 8)
        total_row = last + 2
        page.write_string(total_row, 1, "TOTAL SUBMITTED", label)
        # Totals include cached values for mobile/email viewers that do not recalculate.
        page.write_formula(total_row, 5, f"=SUM(F11:F{last + 1})", integer, total_minutes)
        page.write_formula(total_row, 6, f"=F{total_row + 1}/1440", duration, total_minutes / 1440)
        page.write_formula(total_row, 7, f"=F{total_row + 1}/60", decimal, total_minutes / 60)
        page.print_area(0, 0, total_row, 8)

    name = slugify(sheet.staff.name)[:65] or "employee"
    filename = f"timesheet-{name}-{sheet.month:%Y-%m}-r{sheet.revision}.xlsx"
    return filename, output.getvalue()
