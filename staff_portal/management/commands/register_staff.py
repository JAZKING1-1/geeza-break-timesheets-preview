"""Register an explicitly authorised staff batch without sending email or setting passwords."""
import hashlib
import json
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q

from staff_portal.models import AuditEvent, StaffProfile
from staff_portal.services import audit


class Command(BaseCommand):
    help = "Register a private JSON roster. Defaults to validation only; --apply saves it. Never emails staff."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", type=Path)
        source.add_argument("--from-env", action="store_true")
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.PORTAL_PREVIEW:
            raise CommandError("Personal staff registration is unavailable in demo mode.")
        raw = os.getenv("STAFF_REGISTRATION_BATCH", "") if options["from_env"] else options["file"].read_text(encoding="utf-8")
        if len(raw) > 100_000:
            raise CommandError("Staff batch is too large.")
        try:
            batch = json.loads(raw)
            if not isinstance(batch, dict):
                raise ValueError
            batch_id = str(uuid.UUID(batch["id"]))
            owner_email = batch["owner_email"].strip().lower()
            validate_email(owner_email)
            rows = batch["staff"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise ValueError
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                for field, limit in (("name", 120), ("job_title", 160), ("email", 254)):
                    row[field] = row[field].strip()
                    if not row[field] or len(row[field]) > limit:
                        raise ValueError
                row["email"] = row["email"].lower()
                validate_email(row["email"])
                if row.get("role") not in ("employee", "finance"):
                    raise ValueError
                if "previous_name" in row and (not isinstance(row["previous_name"], str) or len(row["previous_name"]) > 120):
                    raise ValueError
            if len({r["email"] for r in rows}) != len(rows) or len({r["name"].casefold() for r in rows}) != len(rows):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError, ValidationError) as exc:
            raise CommandError("Invalid staff batch. Check its UUID, owner email, unique staff emails, names, job titles and Employee/Finance roles.") from exc
        owner = StaffProfile.objects.select_for_update().filter(
            roster_key="portal-owner", email__iexact=owner_email, enabled=True,
            role=StaffProfile.Role.ADMIN, is_demo=False, user__isnull=False).first()
        if not owner:
            raise CommandError("This database does not have the expected timesheet owner. Nothing was changed.")
        fingerprint = hashlib.sha256(json.dumps(batch, sort_keys=True).encode()).hexdigest()
        previous = AuditEvent.objects.filter(action="Staff batch registered", detail__batch_id=batch_id).first()
        if previous:
            if previous.detail.get("fingerprint") != fingerprint:
                raise CommandError("This batch ID was already used with different staff details.")
            self.stdout.write("Staff registration batch already completed. No accounts, roles or credentials were changed.")
            return
        created = updated = 0
        for row in rows:
            profile = StaffProfile.objects.select_for_update().filter(email__iexact=row["email"]).first()
            if not profile:
                names = Q(name__iexact=row["name"])
                if row.get("previous_name"):
                    names |= Q(name__iexact=row["previous_name"].strip())
                candidates = list(StaffProfile.objects.select_for_update().filter(names))
                if len(candidates) > 1 or (candidates and (candidates[0].email or candidates[0].user_id)):
                    raise CommandError("An existing staff name needs administrator review before registration. Nothing was changed.")
                profile = candidates[0] if candidates else StaffProfile()
            if profile.pk and (not profile.enabled or profile.is_demo or profile.is_administrator):
                raise CommandError("An existing protected or disabled account needs administrator review. Nothing was changed.")
            if profile.user_id:
                if profile.user.email.lower() != row["email"]:
                    raise CommandError("The linked account email does not match. Nothing was changed.")
                # Keep all existing password, MFA, session and activation state.
            else:
                if get_user_model().objects.filter(email__iexact=row["email"]).exists():
                    raise CommandError("An account with this email is already registered separately. Nothing was changed.")
                user = get_user_model()(username="staff-" + uuid.uuid4().hex, email=row["email"], is_active=False)
                user.set_unusable_password()
                user.save()
                profile.user = user
            was_created = not profile.pk
            before = {field: getattr(profile, field) for field in ("name", "job_title", "email", "role")}
            for field in ("name", "job_title", "email", "role"):
                setattr(profile, field, row[field])
            profile.full_clean()
            profile.save()
            audit(owner.user, "Staff account registered" if was_created else "Staff registration updated", profile,
                  batch_id=batch_id, before=before, role=profile.role, email_sent=False)
            created += int(was_created)
            updated += int(not was_created)
        if not options["apply"]:
            transaction.set_rollback(True)
            self.stdout.write(f"Validated {len(rows)} staff accounts; {created} new, {updated} existing. Nothing saved. No emails sent.")
            return
        audit(owner.user, "Staff batch registered", owner, batch_id=batch_id, fingerprint=fingerprint,
              created=created, updated=updated, count=len(rows), email_sent=False)
        self.stdout.write(f"Staff registration complete: {created} new, {updated} existing. Total {len(rows)}. No passwords reset. No emails sent.")
