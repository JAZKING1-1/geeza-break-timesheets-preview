import csv
import datetime as dt
import email
import email.header
import re
import uuid
from collections import defaultdict
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from .accounts import issue_link
from .bulk import MAX_ROWS, RowErrors, monthly_formset, save_month
from .excel import XLSX_CONTENT_TYPE, submission_workbook
from .exports import finance_workbook, timesheet_archive
from .forms import EntryForm, NewTimesheetForm, ProfileForm, WorkDateForm
from .feedback import feedback_rows
from .models import AuditEvent, PasswordLink, StaffProfile, TimeEntry, Timesheet
from .payroll import payroll_dates
from .security import portal_required
from .services import audit, create_timesheet, delete_draft, delete_entry, hours_decimal, save_entry, transition


def month_value(value=None):
    try:
        return dt.datetime.strptime(value or "", "%Y-%m").date().replace(day=1)
    except ValueError:
        return timezone.localdate().replace(day=1)


def revision_value(request):
    try:
        return int(request.POST.get("revision", "-1"))
    except ValueError:
        return -1


def permitted_sheet(request, pk):
    sheets = Timesheet.objects.select_related("staff__user").prefetch_related("entries__category")
    if not request.staff.can_review:
        sheets = sheets.filter(staff=request.staff)
    return get_object_or_404(sheets, pk=pk)


@portal_required()
def dashboard(request):
    month = month_value(request.GET.get("month"))
    sheets = list(Timesheet.objects.filter(staff=request.staff).prefetch_related("entries"))
    month_sheets = [item for item in sheets if item.month == month]
    current = next((item for item in month_sheets if item.editable), next(iter(month_sheets), None))
    minutes = sum(item.total_minutes for item in month_sheets)
    return render(request, "staff_portal/dashboard.html", {
        "current": current, "sheets": sheets, "month": month, "payroll": payroll_dates(month),
        "month_count": len(month_sheets), "month_total": f"{minutes // 60}h {minutes % 60:02d}m",
        "pending_count": Timesheet.objects.filter(status="submitted", month=month).exclude(staff=request.staff).count() if request.staff.can_review else 0})


@require_http_methods(["GET", "POST"])
@portal_required()
def new_sheet(request):
    form = NewTimesheetForm(request.POST if request.method == "POST" else None,
                            initial={"work_date": timezone.localdate(), "creation_token": uuid.uuid4()})
    status = 200
    if request.method == "POST":
        if form.is_valid():
            day = form.cleaned_data["work_date"]
            try:
                item = create_timesheet(request.user, day, form.cleaned_data["creation_token"])
            except (ValidationError, OperationalError, IntegrityError) as exc:
                form.add_error(None, exc if isinstance(exc, ValidationError) else "Another change is being saved. Please try again.")
                status = 409
            else:
                url = reverse("staff:sheet", args=[item.pk])
                return redirect(f"{url}?date={day.isoformat()}#entry-grid")
        else:
            status = 400
    return render(request, "staff_portal/new_sheet.html", {"form": form}, status=status)


@portal_required()
def sheet(request, pk):
    item = permitted_sheet(request, pk)
    own = item.staff_id == request.staff.pk
    can_edit = own and item.editable
    picked = WorkDateForm({"work_date": request.GET.get("date", "")})
    selected_date = picked.cleaned_data["work_date"] if picked.is_valid() else None
    if selected_date and selected_date.replace(day=1) != item.month:
        selected_date = None
    today = timezone.localdate()
    picker_date = selected_date or (today if today.replace(day=1) == item.month else item.month)
    editor = monthly_formset(item, request.POST if request.method == "POST" else None, selected_date) if can_edit else None
    errors, status = [], 200
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        action = request.POST.get("action", "save")
        if action == "add_rows" and editor.management_form.is_valid():
            data = request.POST.copy()
            previous_count = editor.total_form_count()
            new_count = min(MAX_ROWS, previous_count + 3)
            data["rows-TOTAL_FORMS"] = new_count
            for index in range(previous_count, new_count):
                for field in ("break_minutes", "start_fold", "end_fold"):
                    data[f"rows-{index}-{field}"] = "0"
            editor = monthly_formset(item, data)
        elif action not in ("save", "submit"):
            errors.append("Choose Save draft or Submit timesheet.")
            status = 400
        elif editor.is_valid():
            try:
                save_month(request.user, item.pk, revision_value(request), editor.cleaned_data, submit=action == "submit")
            except RowErrors as exc:
                for index, row_errors in exc.rows.items():
                    editor.forms[index].add_error(None, row_errors)
                errors.extend(exc.messages)
                status = 400
            except ValidationError as exc:
                errors.extend(exc.messages)
                status = 409
            except (OperationalError, IntegrityError):
                errors.append("Another change was saved at the same time. Reload the timesheet and try again.")
                status = 409
            else:
                messages.success(request, "Your timesheet has been submitted for review." if action == "submit" else "All your rows have been saved as a draft.")
                return redirect("staff:sheet", pk=pk)
        else:
            status = 400
    return render(request, "staff_portal/sheet.html", {
        "sheet": item, "own": own, "can_edit": can_edit, "editor": editor,
        "payroll": payroll_dates(item.month),
        "work_date_form": WorkDateForm(initial={"work_date": picker_date}),
        "editor_errors": errors, "editor_revision": revision_value(request) if request.method == "POST" else item.revision,
        "can_review": request.staff.can_review and not own,
        "feedback_rows": feedback_rows(item) if not own else [], "can_read_feedback": request.staff.is_administrator,
        "notification": item.notifications.first(),
        "events": item.events.select_related("actor").all()[:100]}, status=status)


@require_http_methods(["GET", "POST"])
@portal_required()
def delete_sheet(request, pk):
    # Even Finance and Administrators may delete only their own unsubmitted drafts.
    item = get_object_or_404(Timesheet, pk=pk, staff=request.staff)
    if not item.deletable or item.notifications.exists():
        raise PermissionDenied("Submitted payroll records cannot be deleted.")
    errors, status = [], 200
    if request.method == "POST":
        if request.POST.get("confirm") != "delete":
            errors, status = ["Confirm deletion using the Delete draft button below."], 400
        else:
            try:
                delete_draft(request.user, pk, revision_value(request))
            except (ValidationError, OperationalError, IntegrityError) as exc:
                errors = exc.messages if isinstance(exc, ValidationError) else ["Another change is being saved. Reload this page and try again."]
                status = 409
            else:
                messages.success(request, f"Your {item.month:%B %Y} draft was deleted. You can start a new timesheet for this month.")
                return redirect("staff:dashboard")
    return render(request, "staff_portal/delete_sheet.html", {
        "sheet": item, "own": True, "errors": errors,
        "revision": revision_value(request) if request.method == "POST" else item.revision,
    }, status=status)


@portal_required()
def entry(request, pk, entry_id=None):
    item = permitted_sheet(request, pk)
    if item.staff_id != request.staff.pk or not item.editable:
        raise PermissionDenied
    if request.method == "GET" and not entry_id:
        return redirect("staff:sheet", pk=pk)
    instance = get_object_or_404(TimeEntry, pk=entry_id, timesheet=item) if entry_id else TimeEntry(timesheet=item)
    today = timezone.localdate()
    day = today if today.replace(day=1) == item.month else item.month
    form = EntryForm(request.POST or None, instance=instance, initial={
        "revision": item.revision, "date": day, "end_date": day, "start_time": "09:00",
        "end_time": "17:00", "break_minutes": 30} if not entry_id else {"revision": item.revision})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            save_entry(request.user, item.pk, form.cleaned_data["revision"], form.save(commit=False))
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 409
        except (OperationalError, IntegrityError):
            form.add_error(None, "Another change was saved at the same time. Reload the timesheet and try again.")
            status = 409
        else:
            messages.success(request, "Your shift has been saved.")
            return redirect("staff:sheet", pk=pk)
    return render(request, "staff_portal/entry.html", {"form": form, "sheet": item, "editing": bool(entry_id)}, status=status)


@require_POST
@portal_required()
def remove_entry(request, pk, entry_id):
    permitted_sheet(request, pk)
    try:
        delete_entry(request.user, pk, revision_value(request), entry_id)
        messages.success(request, "The shift was removed.")
    except (ValidationError, OperationalError, TimeEntry.DoesNotExist) as exc:
        messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "The timesheet changed. Please try again.")
    return redirect("staff:sheet", pk=pk)


@require_POST
@portal_required()
def sheet_action(request, pk):
    permitted_sheet(request, pk)
    try:
        transition(request.user, pk, revision_value(request), request.POST.get("action"), request.POST.get("reason", ""))
        messages.success(request, "The timesheet has been updated.")
    except (ValidationError, OperationalError) as exc:
        messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "Another change is being saved. Reload and try again.")
    return redirect("staff:sheet", pk=pk)


def report_data(request):
    month = month_value(request.GET.get("month"))
    profiles = StaffProfile.objects.select_related("user").filter(Q(enabled=True) | Q(timesheets__month=month)).distinct()
    staff_id = request.GET.get("employee", "")
    selected_status = request.GET.get("status", "")
    # An invalid filter must never silently broaden a finance download.
    if staff_id and not (staff_id.isascii() and staff_id.isdecimal() and len(staff_id) <= 18):
        raise Http404("Choose an employee from the list.")
    if selected_status not in ("", "draft", "submitted", "approved", "returned", "missing", "not_activated", "needs_chasing"):
        raise Http404("Choose a valid status.")
    if request.GET.get("month") and request.GET["month"] != month.strftime("%Y-%m"):
        raise Http404("Choose a valid month.")
    if staff_id:
        profiles = profiles.filter(pk=int(staff_id))
    sheet_map = defaultdict(list)
    for item in Timesheet.objects.filter(month=month, staff__in=profiles).select_related("staff").prefetch_related("entries__category"):
        sheet_map[item.staff_id].append(item)
    rows, categories = [], defaultdict(lambda: {"approved": 0, "pending": 0})
    totals = {"approved": 0, "pending": 0, "submitted": 0, "missing": 0, "returned": 0, "chasing": 0}
    chasing_staff = set()
    for profile in profiles:
        for sheet in sheet_map.get(profile.pk, [None]):
            status = sheet.status if sheet else ("missing" if profile.user_id and profile.user.is_active else "not_activated")
            needs_chasing = profile.enabled and status in ("draft", "returned", "missing", "not_activated")
            if selected_status == "needs_chasing" and not needs_chasing:
                continue
            if selected_status and selected_status != "needs_chasing" and selected_status != status:
                continue
            minutes = sheet.total_minutes if sheet else 0
            rows.append({"profile": profile, "sheet": sheet, "status": status,
                         "label": sheet.get_status_display() if sheet else ("Missing" if status == "missing" else "Not activated"),
                         "minutes": minutes, "hours": hours_decimal(minutes), "needs_chasing": needs_chasing})
            if needs_chasing:
                chasing_staff.add(profile.pk)
            if status in ("submitted", "missing", "returned"):
                totals[status] += 1
            if sheet:
                bucket = "approved" if status == "approved" else "pending"
                totals[bucket] += minutes
                for entry in sheet.entries.all():
                    categories[entry.category.name][bucket] += entry.minutes
    totals["chasing"] = len(chasing_staff)
    return {"month": month, "payroll": payroll_dates(month), "rows": rows, "employee_filter": staff_id, "status_filter": selected_status,
            "employees": StaffProfile.objects.all(), "totals": totals,
            "sheet_count": sum(row["sheet"] is not None for row in rows),
            "employee_count": len({row["profile"].pk for row in rows}),
            "download_query": urlencode({"month": month.strftime("%Y-%m"), "employee": staff_id, "status": selected_status}),
            "approved_hours": hours_decimal(totals["approved"]), "pending_hours": hours_decimal(totals["pending"]),
            "category_rows": [{"name": name, "approved": hours_decimal(t["approved"]),
                               "pending": hours_decimal(t["pending"])} for name, t in sorted(categories.items())]}


@portal_required("review")
def reports(request):
    return render(request, "staff_portal/reports.html", report_data(request))


def csv_safe(value):
    text = str(value)
    return "'" + text if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")) else text


@portal_required("review")
def export_report(request):
    data = report_data(request)
    detail = request.GET.get("detail") == "1"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="geezabreak-{data["month"]:%Y-%m}-{"entries" if detail else "summary"}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    if detail:
        writer.writerow(["Month", "Employee", "Job title", "Status", "Date", "Start", "End date", "End",
                         "Start occurrence", "End occurrence", "Unpaid break minutes", "Work category", "Worked minutes", "Hours", "Purpose", "Reference"])
        for row in data["rows"]:
            if row["sheet"]:
                for entry in row["sheet"].entries.all():
                    writer.writerow([csv_safe(v) for v in [
                        data["month"].strftime("%Y-%m"), row["profile"].name, row["profile"].job_title,
                        row["label"], entry.date, entry.start_time, entry.end_date, entry.end_time,
                        entry.start_fold + 1, entry.end_fold + 1, entry.break_minutes, entry.category.name,
                        entry.minutes, hours_decimal(entry.minutes), entry.notes, row["sheet"].reference]])
    else:
        writer.writerow(["Month", "Employee", "Job title", "Status", "Worked minutes", "Hours", "Approved hours", "Pending hours", "Reference"])
        for row in data["rows"]:
            writer.writerow([csv_safe(v) for v in [data["month"].strftime("%Y-%m"), row["profile"].name,
                row["profile"].job_title, row["label"], row["minutes"], row["hours"],
                row["hours"] if row["status"] == "approved" else "0.00",
                row["hours"] if row["status"] != "approved" else "0.00",
                row["sheet"].reference if row["sheet"] else ""]])
    audit(request.user, "Report exported", request.staff, month=str(data["month"]), detailed=detail,
          employee=data["employee_filter"], status=data["status_filter"])
    return response


@require_http_methods(["GET"])
@portal_required("review")
def export_workbooks(request):
    data = report_data(request)
    kind = request.GET.get("format", "xlsx")
    if kind not in ("xlsx", "zip"):
        raise Http404
    filename, content = timesheet_archive(data) if kind == "zip" else finance_workbook(data)
    response = HttpResponse(content, content_type="application/zip" if kind == "zip" else XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    audit(request.user, "Finance workbook downloaded", request.staff,
          month=str(data["month"]), format=kind, employee=data["employee_filter"],
          status=data["status_filter"], timesheets=data["sheet_count"])
    return response


@require_http_methods(["GET"])
@portal_required()
def download_sheet(request, pk):
    item = permitted_sheet(request, pk)
    filename, content = submission_workbook(item, submitted_snapshot=False)
    response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    audit(request.user, "Timesheet downloaded", item.staff, item, revision=item.revision, status=item.status)
    return response


@portal_required("admin")
def staff_list(request):
    profiles = list(StaffProfile.objects.select_related("user"))
    return render(request, "staff_portal/staff_list.html", {
        "profiles": profiles,
        "active_count": sum(p.account_status == "Active" for p in profiles),
        "setup_count": sum(p.enabled and p.account_status != "Active" for p in profiles),
        "events": AuditEvent.objects.select_related("staff", "actor").all()[:30]})


@portal_required("admin")
def staff_edit(request, pk=None):
    profile = get_object_or_404(StaffProfile, pk=pk) if pk else StaffProfile()
    old_email = profile.email
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        if profile.pk == request.staff.pk and (not updated.enabled or updated.role != StaffProfile.Role.ADMIN):
            form.add_error(None, "You cannot disable or remove administrator access from your own account.")
        else:
            with transaction.atomic():
                if pk:
                    StaffProfile.objects.select_for_update().get(pk=pk)
                updated.save()
                if updated.user_id:
                    user = updated.user
                    if old_email != updated.email:
                        user.set_unusable_password()
                        user.is_active = False
                        PasswordLink.objects.filter(staff=updated, used_at__isnull=True).update(used_at=timezone.now())
                    else:
                        user.is_active = updated.enabled and user.has_usable_password()
                    user.email = updated.email or ""
                    user.save()
                audit(request.user, "Staff profile updated" if pk else "Staff profile added", updated,
                      role=updated.role, enabled=updated.enabled, email_changed=old_email != updated.email)
            messages.success(request, "Staff details saved. Send an invitation when the email is confirmed.")
            return redirect("staff:staff_list")
    return render(request, "staff_portal/staff_edit.html", {"form": form, "profile": profile})


@require_POST
@portal_required("admin")
def invite(request, pk):
    profile = get_object_or_404(StaffProfile, pk=pk)
    try:
        issue_link(profile, "setup", request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        messages.error(request, "The invitation could not be delivered. The account has not been activated; try again later.")
    else:
        messages.success(request, "Invitation saved in local email previews." if settings.PORTAL_LOCAL else "Invitation sent.")
    return redirect("staff:staff_list")


@portal_required("admin")
def local_emails(request):
    if not (settings.PORTAL_LOCAL or settings.PORTAL_PREVIEW):
        raise Http404
    previews = []
    directory = settings.EMAIL_FILE_PATH
    if directory.exists():
        for path in sorted(directory.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:25]:
            parsed = email.message_from_string(path.read_text(encoding="utf-8"))
            # Only the local account backend writes plain text emails.
            part = next((p for p in parsed.walk() if p.get_content_type() == "text/plain"), parsed)
            body = part.get_payload(decode=True)
            body = body.decode(part.get_content_charset() or "utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
            match = re.search(re.escape(settings.PORTAL_BASE_URL.rstrip("/")) + r"/staff/(?:timesheets/\d+|set-password/[A-Za-z0-9_-]+)/", body)
            previews.append({"subject": str(email.header.make_header(email.header.decode_header(parsed.get("Subject", "")))),
                             "to": parsed.get("To"), "body": body, "url": match.group(0) if match else None})
    return render(request, "staff_portal/emails.html", {"previews": previews})


@require_POST
@portal_required()
def keep_session(request):
    return JsonResponse({"active": True})
