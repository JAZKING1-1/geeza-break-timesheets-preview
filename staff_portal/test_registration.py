import copy
import io
import json
import os
import re
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import AuditEvent, PasswordLink, StaffProfile


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, PORTAL_CAPTCHA_MODE="simulate",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StaffRegistrationTests(TestCase):
    def setUp(self):
        self.owner_user = get_user_model().objects.create_user("owner", "owner@example.test", "Owner-secret-2026!")
        self.owner = StaffProfile.objects.create(user=self.owner_user, name="Owner", job_title="Administrator",
                                                email=self.owner_user.email, role="administrator", roster_key="portal-owner")
        self.batch = {"id": str(uuid.uuid4()), "owner_email": self.owner.email, "staff": [
            {"name": "Alex Example", "email": "Alex@example.test", "job_title": "Support", "role": "employee"},
            {"name": "Finley Example", "email": "finley@example.test", "job_title": "Finance", "role": "finance"}]}

    def register(self, apply=False, batch=None):
        output = io.StringIO()
        with patch.dict(os.environ, {"STAFF_REGISTRATION_BATCH": json.dumps(batch or self.batch)}):
            call_command("register_staff", from_env=True, apply=apply, stdout=output)
        return output.getvalue()

    def test_validation_rolls_back_everything_and_sends_no_mail(self):
        self.assertIn("Nothing saved", self.register())
        self.assertEqual(StaffProfile.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(AuditEvent.objects.exists())
        self.assertFalse(PasswordLink.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_registration_creates_private_accounts_without_passwords_or_emails(self):
        self.assertIn("2 new, 0 existing", self.register(apply=True))
        alex = StaffProfile.objects.get(email="alex@example.test")
        finley = StaffProfile.objects.get(email="finley@example.test")
        for profile in (alex, finley):
            self.assertFalse(profile.user.is_active)
            self.assertFalse(profile.user.has_usable_password())
            self.assertTrue(profile.enabled)
            self.assertFalse(profile.is_demo)
            self.assertEqual(profile.account_status, "Awaiting password")
        self.assertFalse(alex.can_review)
        self.assertTrue(finley.can_review)
        self.assertFalse(finley.is_administrator)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(PasswordLink.objects.exists())
        self.assertEqual(AuditEvent.objects.filter(action="Staff batch registered").count(), 1)

    def test_same_batch_is_idempotent_and_changed_batch_id_is_rejected(self):
        self.register(apply=True)
        snapshots = list(get_user_model().objects.values_list("pk", "password"))
        self.assertIn("already completed", self.register(apply=True))
        self.assertEqual(list(get_user_model().objects.values_list("pk", "password")), snapshots)
        self.batch["staff"][0]["role"] = "finance"
        with self.assertRaisesMessage(CommandError, "different staff details"):
            self.register(apply=True)
        self.assertEqual(StaffProfile.objects.get(email="alex@example.test").role, "employee")

    def test_existing_password_mfa_and_activation_are_preserved(self):
        user = get_user_model().objects.create_user("alex", "alex@example.test", "Existing-secret-2026!")
        profile = StaffProfile.objects.create(user=user, name="Alex Example", email=user.email, job_title="Support")
        device = TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=True)
        old_hash = user.password
        self.assertIn("1 new, 1 existing", self.register(apply=True))
        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(profile.user_id, user.pk)
        self.assertEqual(user.password, old_hash)
        self.assertTrue(user.is_active)
        self.assertTrue(TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists())
        self.assertFalse(PasswordLink.objects.exists())

    def test_previous_name_matches_only_unused_placeholder(self):
        profile = StaffProfile.objects.create(name="Alex Previous", job_title="Support")
        self.batch["staff"][0]["previous_name"] = "Alex Previous"
        self.register(apply=True)
        profile.refresh_from_db()
        self.assertEqual(profile.name, "Alex Example")
        self.assertEqual(profile.email, "alex@example.test")
        self.assertIsNotNone(profile.user_id)
        self.assertEqual(StaffProfile.objects.count(), 3)

    def test_conflicting_second_row_rolls_back_first_row_and_preserves_existing(self):
        profile = StaffProfile.objects.create(name="Finley Example", email="different@example.test", job_title="Support")
        with self.assertRaisesMessage(CommandError, "administrator review"):
            self.register(apply=True)
        self.assertFalse(StaffProfile.objects.filter(email="alex@example.test").exists())
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(AuditEvent.objects.exists())
        profile.refresh_from_db()
        self.assertEqual(profile.email, "different@example.test")

    def test_wrong_owner_protected_disabled_and_duplicate_records_fail_closed(self):
        for change in ("owner", "role", "duplicate", "disabled", "administrator", "demo"):
            with self.subTest(change=change):
                batch = copy.deepcopy(self.batch)
                profile = None
                if change == "owner":
                    batch["owner_email"] = "other@example.test"
                elif change == "role":
                    batch["staff"][0]["role"] = "administrator"
                elif change == "duplicate":
                    batch["staff"][1]["email"] = batch["staff"][0]["email"].lower()
                else:
                    profile = StaffProfile.objects.create(name="Alex Example", job_title="Support", email="alex@example.test",
                        enabled=change != "disabled", is_demo=change == "demo",
                        role="administrator" if change == "administrator" else "employee")
                with self.assertRaises(CommandError):
                    self.register(apply=True, batch=batch)
                self.assertEqual(get_user_model().objects.count(), 1)
                if profile:
                    profile.delete()

    @override_settings(PORTAL_PREVIEW=True)
    def test_registration_cannot_run_in_demo_mode(self):
        with self.assertRaisesMessage(CommandError, "demo mode"):
            self.register(apply=True)

    def test_forgot_password_activates_registered_employee_then_uses_normal_reset(self):
        self.register(apply=True)
        response = self.client.post(reverse("staff:reset_password"), {"email": "ALEX@example.test", "captcha": "pass"})
        self.assertContains(response, "If this email is registered for staff access")
        self.assertEqual(mail.outbox[0].to, ["alex@example.test"])
        self.assertEqual(PasswordLink.objects.get().purpose, "setup")
        token = re.search(r"/set-password/([^/]+)/", mail.outbox[0].body).group(1)
        self.client.get(reverse("staff:password_link", args=[token]))
        response = self.client.post(reverse("staff:set_password"), {
            "new_password1": "Private-staff-2026!", "new_password2": "Private-staff-2026!"})
        self.assertRedirects(response, reverse("staff:login"))
        response = self.client.post(reverse("staff:login"), {
            "email": "alex@example.test", "password": "Private-staff-2026!", "captcha": "pass"})
        self.assertRedirects(response, reverse("staff:dashboard"))
        self.assertEqual(self.client.get(reverse("staff:reports")).status_code, 403)
        self.client.logout()
        self.client.post(reverse("staff:reset_password"), {"email": "alex@example.test", "captcha": "pass"})
        self.assertEqual(PasswordLink.objects.latest("pk").purpose, "reset")

    def test_reset_for_unknown_disabled_or_demo_address_sends_nothing(self):
        for address, enabled, demo in [("disabled@example.test", False, False), ("demo@example.test", True, True)]:
            StaffProfile.objects.create(name="Unavailable", job_title="Support", email=address, enabled=enabled, is_demo=demo)
        for email in ("disabled@example.test", "demo@example.test", "unknown@example.test"):
            response = self.client.post(reverse("staff:reset_password"), {"email": email, "captcha": "pass"})
            self.assertContains(response, "If this email is registered for staff access")
        self.assertFalse(PasswordLink.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_first_password_requests_keep_captcha_and_rate_limit(self):
        self.register(apply=True)
        url = reverse("staff:reset_password")
        self.client.post(url, {"email": "alex@example.test", "captcha": "fail"})
        self.assertFalse(PasswordLink.objects.exists())
        for _ in range(4):
            self.client.post(url, {"email": "alex@example.test", "captcha": "pass"})
        response = self.client.post(url, {"email": "alex@example.test", "captcha": "pass"})
        self.assertContains(response, "Too many requests")
        self.assertEqual(len(mail.outbox), 4)
