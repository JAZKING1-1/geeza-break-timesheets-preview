"""Create the initial administrator on a fresh database, with a one-use setup link."""
import datetime as dt
import os
import secrets
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from staff_portal.models import PasswordLink, StaffProfile
from staff_portal.security import token_digest
from staff_portal.service_catalog import sync_service_catalog
from staff_portal.services import audit


class Command(BaseCommand):
    help = "Initialise a fresh staff portal with one personal administrator. Never resets existing credentials."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=os.getenv("BOOTSTRAP_OWNER_EMAIL", ""))
        parser.add_argument("--name", default=os.getenv("BOOTSTRAP_OWNER_NAME", ""))

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.PORTAL_PREVIEW:
            raise CommandError("Use a separate development or production database for personal staff accounts.")
        email, name = options["email"].strip().lower(), options["name"].strip()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("Provide a complete owner email address.") from exc
        if not name or len(name) > 120:
            raise CommandError("Provide the owner's name (up to 120 characters).")
        if StaffProfile.objects.filter(is_demo=True).exists():
            raise CommandError("Demo data is present. Point this deployment at a fresh, separate database.")
        owner = StaffProfile.objects.select_for_update().filter(roster_key="portal-owner").first()
        if owner:
            if owner.email != email:
                raise CommandError("The initial owner already exists with a different address. No access was changed.")
            sync_service_catalog()
            self.stdout.write("The personal owner account already exists. Credentials and setup-link expiry were not changed.")
            return
        if StaffProfile.objects.exists() or get_user_model().objects.exists():
            raise CommandError("Initial owner setup requires a fresh database. Existing users were not changed.")
        token = os.getenv("BOOTSTRAP_OWNER_TOKEN", "")
        if settings.PORTAL_LOCAL and not token:
            token = secrets.token_urlsafe(32)
        if len(token) < 32:
            raise CommandError("Set a private BOOTSTRAP_OWNER_TOKEN of at least 32 characters before first startup.")
        user = get_user_model()(username="staff-" + uuid.uuid4().hex, email=email, is_active=False)
        user.set_unusable_password()
        user.save()
        owner = StaffProfile.objects.create(user=user, name=name, email=email, job_title="Portal administrator",
                                            role=StaffProfile.Role.ADMIN, is_demo=False, roster_key="portal-owner")
        PasswordLink.objects.create(staff=owner, token_hash=token_digest(token), purpose="setup",
                                    expires_at=timezone.now() + dt.timedelta(hours=24))
        sync_service_catalog()
        audit(None, "Initial administrator created", owner)
        if settings.PORTAL_LOCAL:
            url = settings.PORTAL_BASE_URL.rstrip("/") + reverse("staff:password_link", args=[token])
            destination = settings.LOCAL_DATA / "owner-setup-link.txt"
            transaction.on_commit(lambda: destination.write_text(
                f"Personal account: {email}\n\nChoose your own password using this private link.\n"
                f"{url}\n\nIt expires in 24 hours and works once. Keep this file private.\n", encoding="utf-8"))
        self.stdout.write("Personal administrator created. Use the private, one-use setup link within 24 hours.")
