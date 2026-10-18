from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .feedback import FeedbackForm, current_complete, save_feedback
from .models import SessionFeedback, TimeEntry
from .security import portal_required
from .views import revision_value


class FeedbackFilterForm(forms.Form):
    month = forms.DateField(label="Session month (optional)", required=False, input_formats=["%Y-%m"],
                            widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}))
    status = forms.ChoiceField(label="Show", choices=[("open", "To do"), ("submitted", "Submitted"), ("all", "All sessions")])


@require_http_methods(["GET"])
@portal_required()
def feedback_home(request):
    form = FeedbackFilterForm({"month": request.GET.get("month", ""), "status": request.GET.get("status", "open")})
    entries = TimeEntry.objects.filter(timesheet__staff=request.staff).select_related(
        "timesheet", "category", "feedback").defer("feedback__answers").order_by("-date", "-start_time", "-pk")
    rows = []
    if form.is_valid():
        month = form.cleaned_data["month"]
        if month:
            entries = entries.filter(date__year=month.year, date__month=month.month)
        for entry in entries:
            record = getattr(entry, "feedback", None)
            complete = bool(record) and current_complete(entry, record)
            if (form.cleaned_data["status"] == "open" and complete) or (form.cleaned_data["status"] == "submitted" and not complete):
                continue
            rows.append({"entry": entry, "complete": complete,
                         "state": "Submitted" if complete else "Needs review" if record and record.completed_at else "Draft" if record else "Not started"})
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(request, "staff_portal/feedback_home.html", {
        "filter_form": form, "page": page, "feedback_area": True,
        "filter_query": urlencode(form.data),
    })


@require_http_methods(["GET", "POST"])
@portal_required()
def session_feedback(request, pk, entry_id):
    entries = TimeEntry.objects.select_related("timesheet__staff", "category")
    if not request.staff.is_administrator:
        entries = entries.filter(timesheet__staff=request.staff)
    entry = get_object_or_404(entries, pk=entry_id, timesheet_id=pk)
    sheet = entry.timesheet
    can_edit = sheet.staff_id == request.staff.pk
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
            form.add_error(None, "Choose Save feedback draft or Submit feedback.")
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
                messages.success(request, "Feedback submitted. Your timesheet status has not changed." if complete
                                 else "Feedback draft saved. You can finish it from Feedback whenever you are ready.")
                return redirect("staff:feedback_home")
        else:
            status = 400
    if not can_edit:
        for field in form.fields.values():
            field.disabled = True
    return render(request, "staff_portal/feedback.html", {
        "form": form, "entry": entry, "sheet": sheet, "can_edit": can_edit,
        "complete": current_complete(entry, record), "feedback_area": True,
        "revision": revision_value(request) if request.method == "POST" else sheet.revision,
    }, status=status)
