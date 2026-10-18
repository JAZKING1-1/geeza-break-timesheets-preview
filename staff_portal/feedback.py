"""Respite Feedback from the 'finally done' project. See FEEDBACK_FORM.md."""
import hashlib
import json

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import SessionFeedback, TimeEntry
from .services import audit, entry_snapshot, locked_sheet

YES_NO = [("", "Choose an answer"), ("yes", "Yes"), ("no", "No")]
SCHEMA_VERSION = 1

# Stable field keys keep stored answers readable when presentation changes.
# Unknown historical dropdown options are not guessed: use a text field instead.
SECTIONS = [
    ("Session details", [
        ("session_date", "Session date", "date"),
        ("concerns", "Were there any concerns during this session", "yesno"),
        ("reported", "Did you report your concerns to line manager?", "yesno"),
        ("provider", "Respite provider", "text"),
        ("session_completed", "Session completed?", "yesno"),
        ("arrival_date", "Arrival date", "date"),
        ("arrival_time", "Arrival time", "time"),
        ("departure_date", "Departure date", "date"),
        ("departure_time", "Departure time", "time"),
        ("planned_times", "Do these match the planned times?", "yesno"),
        ("times_reason", "If not, please detail why", "long"),
    ]),
    ("Child wellbeing", [("mood", "Child mood / engagement", "long")]),
    ("Outdoor Risk Assessment", [
        ("outdoor", "Does this session need an outdoor risk assessment?", "yesno"),
        ("location", "Location", "text"), ("address", "Address", "long"),
        ("premises", "Are the premises", "text"), ("risk_name", "Name", "text"),
        ("risk_level", "Please measure the risk", "text"),
        ("hazards", "Identified hazards", "long"), ("controls", "Control measures", "long"),
        ("action_required", "Action required?", "long"),
        ("assessor", "Person completing assessment", "text"),
        ("assessment_date", "Assessment date", "date"),
        ("risk_comments", "Additional comments", "long"),
        ("family_aware", "Have the family been made aware of the risk?", "yesno"),
        ("coordinator_advised", "Has the respite coordinator been advised?", "yesno"),
    ]),
    ("Prescribed Medication Administration", [
        ("medication", "Does this session need a medication administration record?", "yesno"),
        ("child_name", "Child name", "text"), ("medication_name", "Name of medication", "text"),
        ("medication_reason", "Reason for medication", "long"), ("dosage", "Dosage", "text"),
        ("administration_method", "Administration method", "text"),
        ("prior_time", "Time administered by parent/carer prior to start of respite", "time"),
        ("administered_date", "Date administered (Respite Care)", "date"),
        ("administered_time", "Time administered", "time"),
        ("parent_signature", "Parent/Carer signature", "text"),
    ]),
    ("Session feedback", [
        ("completed_by", "Form completed by", "text"),
        ("ate", "Did the children have anything to eat?", "yesno"),
        ("food", "Please detail what the children ate", "long"),
        ("relationship", "Have you noticed any improvement in the parent/child relationship?", "yesno"),
        ("coping", "Have you noticed any improvement in the family's coping skills, such as a better routine or a cleaner home?", "yesno"),
        ("coping_detail", "Please detail any improvement in the family's coping skills", "long"),
        ("family_feedback", "Any feedback from children/parents about respite sessions?", "yesno"),
        ("family_feedback_detail", "Please detail any feedback from children/parents", "long"),
        ("provider_feedback", "Respite provider feedback", "long"),
    ]),
    ("GIRFEC and activities", [
        ("safe", "Was the child safe during the session?", "yesno"),
        ("safe_detail", "Please detail how the child was kept safe", "long"),
        ("healthy", "Was the child supported to be healthy during the session?", "yesno"),
        ("healthy_detail", "Please detail how the child was supported to be healthy", "long"),
        ("achieving", "Was the child supported to be achieving during the session?", "yesno"),
        ("achieving_detail", "Please detail how the child was supported to be achieving", "long"),
        ("nurtured", "Was the child supported to feel nurtured during the session?", "yesno"),
        ("nurtured_detail", "Please detail how the child was supported to feel nurtured", "long"),
        ("active", "Was the child supported to be active during the session?", "yesno"),
        ("active_detail", "Please detail how the child was supported to be active", "long"),
        ("respected", "Was the child supported to feel respected during the session?", "yesno"),
        ("respected_detail", "Please detail how the child was supported to feel respected", "long"),
        ("responsible", "Was the child supported to be responsible during the session?", "yesno"),
        ("responsible_detail", "Please detail how the child was supported to be responsible", "long"),
        ("included", "Was the child supported to feel included during the session?", "yesno"),
        ("included_detail", "Please detail how the child was supported to feel included", "long"),
        ("activities", "Activities undertaken", "long"),
        ("provider_signature", "Respite provider signature/name", "text"),
    ]),
]
CORE = {"concerns", "provider", "session_completed", "session_date", "arrival_date", "arrival_time",
        "departure_date", "departure_time", "planned_times", "mood", "outdoor", "medication",
        "completed_by", "ate", "relationship", "coping", "family_feedback", "provider_feedback",
        "activities", "provider_signature"}
GIRFEC = ("safe", "healthy", "achieving", "nurtured", "active", "respected", "responsible", "included")
CORE.update(GIRFEC)


def respite_entry(entry):
    return "respite" in entry.category.name.casefold()


def fingerprint(entry):
    return hashlib.sha256(json.dumps(entry_snapshot(entry), sort_keys=True).encode()).hexdigest()


class FeedbackForm(forms.Form):
    applies = forms.ChoiceField(label="Was this a respite session?", choices=YES_NO, required=False)
    not_applicable_reason = forms.CharField(label="Why is Respite Feedback not applicable?", required=False,
                                           max_length=300, widget=forms.Textarea(attrs={"rows": 2}))
    confirmed = forms.BooleanField(label="I confirm this feedback is accurate and complete for this session.", required=False)

    def __init__(self, *args, entry, complete=False, **kwargs):
        supplied = args[0] if args else kwargs.get("data")
        if supplied is not None:
            data = supplied.copy()
            # Unused branches must not retain or validate irrelevant care details.
            exempt = data.get("applies") == "no" and not respite_entry(entry)
            for heading, specs in SECTIONS:
                skip = exempt or (heading == "Outdoor Risk Assessment" and data.get("outdoor") == "no") or (
                    heading == "Prescribed Medication Administration" and data.get("medication") == "no")
                if skip:
                    for key, _, _ in specs:
                        if key not in ("outdoor", "medication"):
                            data[key] = ""
            if args:
                args = (data, *args[1:])
            else:
                kwargs["data"] = data
        super().__init__(*args, **kwargs)
        self.entry, self.complete = entry, complete
        for heading, specs in SECTIONS:
            for key, label, kind in specs:
                options = {"label": label, "required": False}
                if kind == "yesno":
                    field = forms.ChoiceField(choices=YES_NO, **options)
                elif kind == "date":
                    field = forms.DateField(widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), **options)
                elif kind == "time":
                    field = forms.TimeField(widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}), **options)
                else:
                    field = forms.CharField(max_length=2000 if kind == "long" else 300,
                        widget=forms.Textarea(attrs={"rows": 3}) if kind == "long" else forms.TextInput(), **options)
                self.fields[key] = field
        # These facts come from the saved shift and signed-in employee, not a posted identity.
        facts = {"session_date": entry.date, "provider": entry.timesheet.staff.name,
                 "completed_by": entry.timesheet.staff.name}
        for key, value in facts.items():
            self.fields[key].disabled = True
            self.initial[key] = value
        defaults = {"arrival_date": entry.date, "arrival_time": entry.start_time,
                    "departure_date": entry.end_date, "departure_time": entry.end_time}
        for key, value in defaults.items():
            self.initial.setdefault(key, value)
        if respite_entry(entry):
            self.fields["applies"].disabled = True
            self.initial["applies"] = "yes"
        self.fields["parent_signature"].help_text = "Record an already obtained signature/name only. Do not sign on someone else's behalf."
        self.fields["reported"].help_text = "Required if there were concerns. Report urgent concerns to your line manager directly; this form does not send a safeguarding alert."
        self.fields["prior_time"].help_text = "Leave blank if no medication was given before the session."
        for key, field in self.fields.items():
            field.completion_required = key in CORE or key in ("applies", "confirmed")
            field.widget.attrs["aria-describedby"] = f"id_{key}_helptext id_{key}_error"

    @property
    def sections(self):
        return [{"heading": heading, "fields": [self[key] for key, _, _ in specs]} for heading, specs in SECTIONS]

    def clean(self):
        data = super().clean()
        if not self.complete:
            return data
        def require(key):
            if not data.get(key) and key not in self.errors:
                self.add_error(key, "Complete this before submitting feedback.")
        require("applies")
        require("confirmed")
        if data.get("applies") == "no":
            require("not_applicable_reason")
            if respite_entry(self.entry):
                self.add_error("applies", "Respite work needs session feedback.")
            return data
        for key in CORE:
            require(key)
        for choice, detail, answer in (("concerns", "reported", "yes"), ("planned_times", "times_reason", "no"),
                                      ("ate", "food", "yes"), ("coping", "coping_detail", "yes"),
                                      ("family_feedback", "family_feedback_detail", "yes")):
            if data.get(choice) == answer:
                require(detail)
        for key in GIRFEC:
            if data.get(key) == "yes":
                require(key + "_detail")
        for trigger, heading, optional in (("outdoor", "Outdoor Risk Assessment", {"risk_name", "risk_comments"}),
                                           ("medication", "Prescribed Medication Administration", {"prior_time", "parent_signature"})):
            if data.get(trigger) == "yes":
                specs = next(specs for title, specs in SECTIONS if title == heading)
                for key, _, _ in specs:
                    if key not in optional:
                        require(key)
        if data.get("arrival_date") and data.get("departure_date") and data["departure_date"] < data["arrival_date"]:
            self.add_error("departure_date", "Departure cannot be before arrival.")
        return data

    def payload(self):
        # Save only declared fields; no arbitrary posted keys or Django objects.
        if self.cleaned_data.get("applies") == "no":
            return {key: self.cleaned_data[key] for key in ("applies", "confirmed", "not_applicable_reason")}
        return {key: value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in self.cleaned_data.items()}


def current_complete(entry, record=None):
    if record is None:
        record = SessionFeedback.objects.filter(entry=entry).first()
    return bool(record and record.completed_at and record.schema_version == SCHEMA_VERSION
                and record.entry_fingerprint == fingerprint(entry))


def feedback_rows(sheet):
    records = {r.entry_id: r for r in SessionFeedback.objects.filter(entry__timesheet=sheet).defer("answers")}
    return [{"entry": e, "complete": bool(records.get(e.pk)) and current_complete(e, records[e.pk]),
             "respite": respite_entry(e)} for e in sheet.entries.select_related("category")]


@transaction.atomic
def save_feedback(actor, sheet_id, revision, entry_id, data, *, complete=False):
    sheet, profile = locked_sheet(actor, sheet_id, revision)
    # Care feedback has its own submission flow, including after payroll review.
    # The sheet lock/revision still protects against stale session details or tabs.
    if sheet.staff.user_id != actor.pk:
        raise PermissionDenied
    entry = TimeEntry.objects.select_related("category").get(pk=entry_id, timesheet=sheet)
    form = FeedbackForm(data, entry=entry, complete=complete)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    record, _ = SessionFeedback.objects.update_or_create(entry=entry, defaults={
        "answers": form.payload(), "schema_version": SCHEMA_VERSION,
        "entry_fingerprint": fingerprint(entry), "completed_at": timezone.now() if complete else None})
    # No care answers in generic audit logs, Finance pages or notifications.
    audit(actor, "Session feedback completed" if complete else "Session feedback draft saved",
          sheet.staff, sheet, entry_id=entry.pk, schema_version=SCHEMA_VERSION)
    return record


def confirm_non_respite(sheet):
    """Only after an explicit, fresh checkbox submission within the sheet transaction."""
    for entry in sheet.entries.select_related("category"):
        if respite_entry(entry):
            continue
        record = SessionFeedback.objects.filter(entry=entry).first()
        # Never replace a person's full session feedback with the bulk declaration.
        if record and record.answers.get("applies") == "yes":
            continue
        SessionFeedback.objects.update_or_create(entry=entry, defaults={
            "schema_version": SCHEMA_VERSION, "entry_fingerprint": fingerprint(entry),
            "completed_at": timezone.now(), "answers": {"applies": "no", "confirmed": True,
            "not_applicable_reason": "Staff confirmed this shift did not include a respite session."}})
