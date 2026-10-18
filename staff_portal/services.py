import datetime as dt
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import AuditEvent, StaffProfile, TimeEntry, Timesheet


class EditConflict(ValidationError):
    pass


def audit(actor, action, staff=None, sheet=None, **detail):
    return AuditEvent.objects.create(actor=actor, action=action, staff=staff,
                                     timesheet=sheet, detail=detail)


def hours_decimal(minutes):
    return str((Decimal(minutes) / 60).quantize(Decimal("0.01")))


@transaction.atomic
def create_timesheet(actor, day, token):
    profile = StaffProfile.objects.select_for_update().filter(
        user=actor, enabled=True, user__is_active=True).first()
    if not profile:
        raise PermissionDenied
    sheet, created = Timesheet.objects.get_or_create(
        staff=profile, creation_token=token, defaults={"month": day.replace(day=1)})
    if sheet.month != day.replace(day=1):
        raise ValidationError("This request already created a timesheet for another month. Open Add new timesheet to start another.")
    if created:
        audit(actor, "Timesheet created", profile, sheet)
    return sheet


def locked_sheet(actor, sheet_id, revision):
    profile = StaffProfile.objects.filter(user=actor, enabled=True, user__is_active=True).first()
    if not profile:
        raise PermissionDenied
    sheet = Timesheet.objects.select_for_update().select_related("staff").get(pk=sheet_id)
    if sheet.staff.user_id != actor.pk and not profile.can_review:
        raise PermissionDenied
    if sheet.revision != revision:
        raise EditConflict("This timesheet changed in another window. Reload it before trying again.")
    updated = Timesheet.objects.filter(pk=sheet.pk, revision=revision).update(
        revision=F("revision") + 1, updated_at=timezone.now())
    if updated != 1:
        raise EditConflict("Another change was saved first. Reload the page.")
    sheet.revision += 1
    return sheet, profile


@transaction.atomic
def save_entry(actor, sheet_id, revision, entry):
    sheet, profile = locked_sheet(actor, sheet_id, revision)
    if sheet.staff.user_id != actor.pk or not sheet.editable:
        raise PermissionDenied
    if entry.pk and not TimeEntry.objects.filter(pk=entry.pk, timesheet=sheet).exists():
        raise PermissionDenied
    before = None
    if entry.pk:
        previous = TimeEntry.objects.get(pk=entry.pk)
        before = entry_snapshot(previous)
    entry.timesheet = sheet
    entry.full_clean(exclude=["starts_at", "ends_at", "minutes"])
    entry.save()
    audit(actor, "Entry updated" if before else "Entry added", sheet.staff, sheet,
          before=before, after=entry_snapshot(entry))
    return entry


def entry_snapshot(entry):
    return {key: str(getattr(entry, key)) for key in (
        "date", "start_time", "end_date", "end_time", "break_minutes",
        "category_id", "notes", "minutes", "start_fold", "end_fold")}


@transaction.atomic
def delete_entry(actor, sheet_id, revision, entry_id):
    sheet, profile = locked_sheet(actor, sheet_id, revision)
    if sheet.staff.user_id != actor.pk or not sheet.editable:
        raise PermissionDenied
    entry = TimeEntry.objects.get(pk=entry_id, timesheet=sheet)
    audit(actor, "Entry removed", sheet.staff, sheet, before=entry_snapshot(entry))
    entry.delete()


@transaction.atomic
def delete_draft(actor, sheet_id, revision):
    sheet, profile = locked_sheet(actor, sheet_id, revision)
    # Team review access does not grant permission to discard another person's work.
    if sheet.staff.user_id != actor.pk:
        raise PermissionDenied
    if not sheet.deletable or sheet.notifications.exists():
        raise ValidationError("Only unsubmitted drafts can be deleted. Ask your reviewer to return a submitted timesheet for corrections.")
    # Preserve each history event independently of the removed draft.
    for event in sheet.events.select_for_update():
        event.detail = {**event.detail, "deleted_timesheet_id": sheet.pk, "month": str(sheet.month)}
        event.timesheet = None
        event.save(update_fields=["detail", "timesheet"])
    audit(actor, "Draft timesheet deleted", sheet.staff,
          deleted_timesheet_id=sheet.pk, month=str(sheet.month),
          total_minutes=sheet.total_minutes, shift_count=sheet.entries.count())
    sheet.delete()


@transaction.atomic
def transition(actor, sheet_id, revision, action, reason="", *, notify=True):
    sheet, profile = locked_sheet(actor, sheet_id, revision)
    reason = reason.strip()
    if len(reason) > 500:
        raise ValidationError("Keep the reason to 500 characters.")
    if action == "submit":
        if sheet.staff.user_id != actor.pk or not sheet.editable:
            raise PermissionDenied
        return submit_locked_sheet(actor, sheet, notify=notify)
    elif action in ("approve", "return", "reopen"):
        if not profile.can_review or sheet.staff.user_id == actor.pk:
            raise PermissionDenied
        required = Timesheet.Status.APPROVED if action == "reopen" else Timesheet.Status.SUBMITTED
        if sheet.status != required:
            raise ValidationError("This action is no longer available for this timesheet.")
        if action in ("return", "reopen") and not reason:
            raise ValidationError("Explain what needs correcting before returning or reopening a timesheet.")
        sheet.status = Timesheet.Status.APPROVED if action == "approve" else Timesheet.Status.RETURNED
        sheet.reviewed_by = actor
        sheet.review_note = reason
        sheet.approved_at = timezone.now() if action == "approve" else None
    else:
        raise ValidationError("Unknown action.")
    sheet.save()
    audit(actor, {"submit": "Submitted", "approve": "Approved", "return": "Returned",
                  "reopen": "Reopened"}[action], sheet.staff, sheet, reason=reason, total_minutes=sheet.total_minutes)
    return sheet


def submit_locked_sheet(actor, sheet, *, notify=True):
    """Called only inside a transaction after ownership, status and revision checks."""
    from .notifications import queue_submission
    if not sheet.entries.exists():
        raise ValidationError("Add at least one shift before submitting.")
    from .feedback import require_feedback
    require_feedback(sheet)
    sheet.status = Timesheet.Status.SUBMITTED
    sheet.submitted_at = timezone.now()
    sheet.review_note = ""
    sheet.approved_at = None
    sheet.reviewed_by = None
    sheet.save()
    audit(actor, "Submitted", sheet.staff, sheet, total_minutes=sheet.total_minutes)
    if notify:
        queue_submission(sheet)
    return sheet
