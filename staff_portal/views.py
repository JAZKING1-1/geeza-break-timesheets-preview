import csv
import datetime as dt
import email
import email.header
import re
from collections import defaultdict

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST

from .accounts import issue_link
from .bulk import MAX_ROWS, RowErrors, monthly_formset, save_month
from .forms import EntryForm, MonthForm, ProfileForm, WorkDateForm
from .models import AuditEvent, PasswordLink, StaffProfile, TimeEntry, Timesheet
from .security import portal_required
from .services import audit, delete_entry, hours_decimal, save_entry, transition


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
    current = next((item for item in sheets if item.month == month), None)
    return render(request, "staff_portal/dashboard.html", {
        "current": current, "sheets": sheets, "month": month,
        "pending_count": Timesheet.objects.filter(status="submitted").exclude(staff=request.staff).count() if request.staff.can_review else 0})


@require_POST
@portal_required()
def new_sheet(request):
    choosing_date = "work_date" in request.POST
    form = WorkDateForm(request.POST) if choosing_date else MonthForm(request.POST)
    if form.is_valid():
        day = form.cleaned_data["work_date"] if choosing_date else form.cleaned_data["month"]
        month = day.replace(day=1)
        sheet, created = Timesheet.objects.get_or_create(staff=request.staff, month=month)
        if created:
            audit(request.user, "Timesheet created", request.staff, sheet)
        url = reverse("staff:sheet", args=[sheet.pk])
        return redirect(f"{url}?date={day.isoformat()}#entry-grid" if choosing_date else url)
    messages.error(request, "Choose a valid date, including the day, month and year." if choosing_date else "Choose a valid calendar month.")
    return redirect("staff:dashboard")


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
        "work_date_form": WorkDateForm(initial={"work_date": picker_date}),
        "editor_errors": errors, "editor_revision": revision_value(request) if request.method == "POST" else item.revision,
        "can_review": request.staff.can_review and not own,
        "notification": item.notifications.first(),
        "events": item.events.select_related("actor").all()[:100]}, status=status)


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
    if staff_id.isdigit():
        profiles = profiles.filter(pk=int(staff_id))
    sheet_map = {s.staff_id: s for s in Timesheet.objects.filter(month=month, staff__in=profiles)
                 .select_related("staff").prefetch_related("entries__category")}
    selected_status = request.GET.get("status", "")
    rows, categories = [], defaultdict(lambda: {"approved": 0, "pending": 0})
    totals = {"approved": 0, "pending": 0, "submitted": 0, "missing": 0, "returned": 0}
    for profile in profiles:
        sheet = sheet_map.get(profile.pk)
        status = sheet.status if sheet else ("missing" if profile.user_id and profile.user.is_active else "not_activated")
        if selected_status and selected_status != status:
            continue
        minutes = sheet.total_minutes if sheet else 0
        rows.append({"profile": profile, "sheet": sheet, "status": status,
                     "label": sheet.get_status_display() if sheet else ("Missing" if status == "missing" else "Not activated"),
                     "minutes": minutes, "hours": hours_decimal(minutes)})
        if status in ("submitted", "missing", "returned"):
            totals[status] += 1
        if sheet:
            bucket = "approved" if status == "approved" else "pending"
            totals[bucket] += minutes
            for entry in sheet.entries.all():
                categories[entry.category.name][bucket] += entry.minutes
    return {"month": month, "rows": rows, "employee_filter": staff_id, "status_filter": selected_status,
            "employees": StaffProfile.objects.all(), "totals": totals,
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
                         "Start occurrence", "End occurrence", "Unpaid break minutes", "Work category", "Worked minutes", "Hours", "Purpose"])
        for row in data["rows"]:
            if row["sheet"]:
                for entry in row["sheet"].entries.all():
                    writer.writerow([csv_safe(v) for v in [
                        data["month"].strftime("%Y-%m"), row["profile"].name, row["profile"].job_title,
                        row["label"], entry.date, entry.start_time, entry.end_date, entry.end_time,
                        entry.start_fold + 1, entry.end_fold + 1, entry.break_minutes, entry.category.name,
                        entry.minutes, hours_decimal(entry.minutes), entry.notes]])
    else:
        writer.writerow(["Month", "Employee", "Job title", "Status", "Worked minutes", "Hours", "Approved hours", "Pending hours"])
        for row in data["rows"]:
            writer.writerow([csv_safe(v) for v in [data["month"].strftime("%Y-%m"), row["profile"].name,
                row["profile"].job_title, row["label"], row["minutes"], row["hours"],
                row["hours"] if row["status"] == "approved" else "0.00",
                row["hours"] if row["status"] != "approved" else "0.00"]])
    audit(request.user, "Report exported", request.staff, month=str(data["month"]), detailed=detail,
          employee=data["employee_filter"], status=data["status_filter"])
    return response


@portal_required("admin")
def staff_list(request):
    return render(request, "staff_portal/staff_list.html", {
        "profiles": StaffProfile.objects.select_related("user"), "events": AuditEvent.objects.select_related("staff", "actor").all()[:30]})


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
