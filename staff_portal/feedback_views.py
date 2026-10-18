from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .feedback import FeedbackForm, current_complete, save_feedback
from .models import SessionFeedback, TimeEntry
from .security import portal_required
from .views import revision_value


@require_http_methods(["GET", "POST"])
@portal_required()
def session_feedback(request, pk, entry_id):
    entries = TimeEntry.objects.select_related("timesheet__staff", "category")
    if not request.staff.is_administrator:
        entries = entries.filter(timesheet__staff=request.staff)
    entry = get_object_or_404(entries, pk=entry_id, timesheet_id=pk)
    sheet = entry.timesheet
    can_edit = sheet.staff_id == request.staff.pk and sheet.editable
    record = SessionFeedback.objects.filter(entry=entry).first()
    action = request.POST.get("action", "draft")
    complete = action == "complete"
    form = FeedbackForm(request.POST if request.method == "POST" else None, entry=entry,
                        initial=record.answers if record else {}, complete=complete)
    status = 200
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if action not in ("draft", "complete"):
            form.add_error(None, "Choose Save feedback draft or Complete feedback.")
            status = 400
        elif form.is_valid():
            try:
                save_feedback(request.user, pk, revision_value(request), entry_id, request.POST, complete=complete)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
                status = 409
            except (OperationalError, IntegrityError):
                form.add_error(None, "Another change is being saved. Reload and try again.")
                status = 409
            else:
                messages.success(request, "Feedback complete. Your timesheet is still a draft until you submit it." if complete
                                 else "Feedback draft saved. Complete it before submitting your timesheet.")
                return redirect(reverse("staff:sheet", args=[pk]) + "#session-feedback")
        else:
            status = 400
    if not can_edit:
        for field in form.fields.values():
            field.disabled = True
    return render(request, "staff_portal/feedback.html", {
        "form": form, "entry": entry, "sheet": sheet, "can_edit": can_edit,
        "complete": current_complete(entry, record),
        "revision": revision_value(request) if request.method == "POST" else sheet.revision,
    }, status=status)
