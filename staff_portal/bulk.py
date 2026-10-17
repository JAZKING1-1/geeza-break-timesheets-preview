"""Accessible monthly entry forms and atomic batch editing."""
import calendar

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from .models import TimeEntry, WorkCategory
from .services import audit, entry_snapshot, locked_sheet, submit_locked_sheet


MAX_ROWS = 200
ENTRY_FIELDS = ("date", "start_time", "end_date", "end_time", "break_minutes",
                "category", "notes", "start_fold", "end_fold")


class RowForm(forms.Form):
    entry_id = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput())
    date = forms.DateField(label="Date worked", input_formats=["%Y-%m-%d"],
                          widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    category = forms.ModelChoiceField(label="Service / work type", queryset=WorkCategory.objects.none(), empty_label="Choose service / work type")
    start_time = forms.TimeField(label="Start", input_formats=["%H:%M"], widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    end_time = forms.TimeField(label="Finish", input_formats=["%H:%M"], widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    break_minutes = forms.IntegerField(label="Break (min)", min_value=0, max_value=1439, initial=0,
                                      widget=forms.NumberInput(attrs={"min": 0, "max": 1439}))
    notes = forms.CharField(label="Note (optional)", required=False, max_length=500,
                           widget=forms.Textarea(attrs={"rows": 2, "maxlength": 500, "placeholder": "A short note about your work"}))
    end_date = forms.DateField(label="Finish date", required=False, input_formats=["%Y-%m-%d"],
                               widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    start_fold = forms.TypedChoiceField(label="Start occurrence", choices=TimeEntry._meta.get_field("start_fold").choices,
                                        coerce=int, initial=0, required=False, empty_value=0)
    end_fold = forms.TypedChoiceField(label="Finish occurrence", choices=TimeEntry._meta.get_field("end_fold").choices,
                                      coerce=int, initial=0, required=False, empty_value=0)

    def __init__(self, *args, month, categories, **kwargs):
        super().__init__(*args, **kwargs)
        self.month = month
        last_day = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        self.fields["date"].widget.attrs.update({"min": month.isoformat(), "max": last_day.isoformat()})
        self.fields["category"].queryset = categories
        for name, field in self.fields.items():
            field.widget.attrs["aria-describedby"] = f"entry-help id_{self.prefix}-{name}_error"

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day.replace(day=1) != self.month:
            raise ValidationError("Choose a date in this timesheet's month. To change month or year, open Different month or year? above the entries.")
        return day

    def clean(self):
        data = super().clean()
        data["end_date"] = data.get("end_date") or data.get("date")
        return data


RowFormSet = forms.formset_factory(RowForm, extra=1, can_delete=True, max_num=MAX_ROWS,
                                  validate_max=True, absolute_max=MAX_ROWS)


def monthly_formset(sheet, data=None, selected_date=None):
    entries = list(sheet.entries.all())
    categories = WorkCategory.objects.filter(Q(active=True) | Q(pk__in=[e.category_id for e in entries]))
    initial = []
    for entry in entries:
        row = {key: getattr(entry, key) for key in ENTRY_FIELDS}
        row["entry_id"] = entry.pk
        row["category"] = entry.category_id
        # Same-day shifts follow the selected date unless overnight is explicitly set.
        row["end_date"] = entry.end_date if entry.end_date != entry.date else None
        initial.append(row)
    formset = RowFormSet(data=data, prefix="rows", initial=initial,
                        form_kwargs={"month": sheet.month, "categories": categories})
    if data is None and selected_date and selected_date.replace(day=1) == sheet.month and formset.extra_forms:
        formset.extra_forms[0].initial["date"] = selected_date
    return formset


class RowErrors(ValidationError):
    def __init__(self, rows):
        self.rows = rows
        super().__init__("Check the highlighted rows. Nothing has been saved.")


@transaction.atomic
def save_month(actor, sheet_id, revision, rows, submit=False):
    sheet, profile = locked_sheet(actor, sheet_id, revision)
    if sheet.staff.user_id != actor.pk or not sheet.editable:
        raise PermissionDenied
    if len(rows) > MAX_ROWS:
        raise ValidationError("Use no more than 200 rows in one timesheet.")
    existing = {entry.pk: entry for entry in sheet.entries.all()}
    supplied = [row["entry_id"] for row in rows if row.get("entry_id")]
    if len(supplied) != len(set(supplied)) or set(supplied) != set(existing):
        raise ValidationError("The saved rows have changed or are incomplete. Reload this timesheet before saving.")
    proposed, errors, removed = [], {}, []
    for index, row in enumerate(rows):
        previous = existing.get(row.get("entry_id"))
        if row.get("DELETE"):
            if previous:
                removed.append(previous)
            continue
        if not row.get("date"):
            continue
        entry = TimeEntry(timesheet=sheet, pk=previous.pk if previous else None,
                          **{key: row[key] for key in ENTRY_FIELDS})
        try:
            entry.clean_fields(exclude=["starts_at", "ends_at", "minutes"])
            if not entry.category.active and (not previous or previous.category_id != entry.category_id):
                raise ValidationError("Choose an active work type.")
            entry.clean_timing()
            entry.check_overlap(exclude_sheet=True)
        except ValidationError as exc:
            errors[index] = exc.messages
        else:
            proposed.append((index, entry, previous))
    ordered = sorted(proposed, key=lambda item: item[1].starts_at)
    for first, second in zip(ordered, ordered[1:]):
        if first[1].ends_at > second[1].starts_at:
            errors.setdefault(first[0], []).append(f"This time overlaps row {second[0] + 1}.")
            errors.setdefault(second[0], []).append(f"This time overlaps row {first[0] + 1}.")
    if errors:
        raise RowErrors(errors)
    if submit and not proposed:
        raise ValidationError("Add at least one shift before submitting.")
    for entry in removed:
        audit(actor, "Entry removed", sheet.staff, sheet, before=entry_snapshot(entry))
        entry.delete()
    for index, entry, previous in proposed:
        before = entry_snapshot(previous) if previous else None
        after = entry_snapshot(entry)
        if before != after:
            entry.save()
            audit(actor, "Entry updated" if previous else "Entry added", sheet.staff, sheet, before=before, after=after)
    if submit:
        submit_locked_sheet(actor, sheet)
    return sheet
