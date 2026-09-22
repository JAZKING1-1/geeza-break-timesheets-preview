import io
import os
import tempfile
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .accounts import consume_link
from .forms import PortalPasswordForm
from .models import PasswordLink, StaffProfile, Timesheet
from .security import token_digest


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PersonalOwnerTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = override_settings(LOCAL_DATA=Path(self.temp.name), PORTAL_BASE_URL="http://127.0.0.1:8010")
        self.config.enable()
        self.addCleanup(self.config.disable)

    def bootstrap(self):
        call_command("bootstrap_owner", email="owner@example.org", name="Portal owner", stdout=io.StringIO())

    @patch.dict(os.environ, {"BOOTSTRAP_OWNER_TOKEN": "one-use-test-token-with-over-32-characters"})
    def test_personal_setup_is_private_one_use_and_restart_does_not_reset_it(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.bootstrap()
        owner = StaffProfile.objects.get()
        self.assertEqual(owner.role, "administrator")
        self.assertFalse(owner.is_demo)
        self.assertFalse(owner.user.is_active)
        self.assertFalse(owner.user.has_usable_password())
        self.assertFalse(Timesheet.objects.exists())
        token = os.environ["BOOTSTRAP_OWNER_TOKEN"]
        link = PasswordLink.objects.get()
        self.assertEqual(link.token_hash, token_digest(token))
        self.assertNotEqual(link.token_hash, token)
        self.assertIn(token, (Path(self.temp.name) / "owner-setup-link.txt").read_text())
        self.assertEqual(self.client.get(reverse("staff:password_link", args=[token])).status_code, 302)
        consume_link(link.pk, token_digest(token), "A personal staff password with 2026 length")
        owner.user.refresh_from_db()
        self.assertTrue(owner.user.is_active)
        password_hash = owner.user.password
        self.bootstrap()
        owner.user.refresh_from_db()
        self.assertEqual(owner.user.password, password_hash)
        self.assertEqual(PasswordLink.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("staff:password_link", args=[token])).status_code, 400)

    def test_setup_without_an_attached_user_is_rejected_without_consuming_link(self):
        self.bootstrap()
        owner = StaffProfile.objects.get()
        original_user = owner.user
        owner.user = None
        owner.save(update_fields=["user"])
        link = PasswordLink.objects.get()
        with self.assertRaisesMessage(ValidationError, "expired or has already been used"):
            consume_link(link.pk, link.token_hash, "A personal staff password with 2026 length")
        link.refresh_from_db()
        original_user.refresh_from_db()
        self.assertIsNone(link.used_at)
        self.assertFalse(original_user.is_active)
        self.assertFalse(original_user.has_usable_password())

    def test_cannot_reassign_an_existing_owner_or_bootstrap_over_demo_data(self):
        self.bootstrap()
        with self.assertRaises(CommandError):
            call_command("bootstrap_owner", email="someone-else@example.org", name="Someone else", stdout=io.StringIO())
        self.assertEqual(StaffProfile.objects.count(), 1)
        self.assertEqual(StaffProfile.objects.get().email, "owner@example.org")
        StaffProfile.objects.create(name="Old demo", job_title="Demo", is_demo=True)
        with self.assertRaises(CommandError):
            self.bootstrap()

    @patch.dict(os.environ, {"BOOTSTRAP_OWNER_TOKEN": "password-length-test-token-over-32-characters"})
    def test_setup_accepts_eight_characters_and_rejects_seven(self):
        self.bootstrap()
        token = os.environ["BOOTSTRAP_OWNER_TOKEN"]
        self.client.get(reverse("staff:password_link", args=[token]))
        response = self.client.post(reverse("staff:set_password"), {
            "new_password1": "K7!mQ2z", "new_password2": "K7!mQ2z"})
        self.assertContains(response, "at least 8 characters")
        self.assertIsNone(PasswordLink.objects.get().used_at)
        response = self.client.post(reverse("staff:set_password"), {
            "new_password1": "K7!mQ2zR", "new_password2": "K7!mQ2zR"})
        self.assertRedirects(response, reverse("staff:login"))
        user = StaffProfile.objects.get().user
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("K7!mQ2zR"))

    def test_longer_passwords_remain_valid_and_common_passwords_are_rejected(self):
        user = get_user_model()(username="length-test", email="owner@example.org")
        for password, valid in [("Several uncommon words plus 9!", True), ("password", False), ("12345678", False)]:
            with self.subTest(password_type="long" if valid else "weak"):
                form = PortalPasswordForm(user, data={"new_password1": password, "new_password2": password})
                self.assertEqual(form.is_valid(), valid)

    def test_preview_and_unconfigured_remote_bootstrap_are_rejected(self):
        with override_settings(PORTAL_PREVIEW=True), self.assertRaises(CommandError):
            self.bootstrap()
        with override_settings(PORTAL_LOCAL=False), patch.dict(os.environ, {"BOOTSTRAP_OWNER_TOKEN": ""}), self.assertRaises(CommandError):
            self.bootstrap()
        self.assertFalse(get_user_model().objects.exists())

    def test_production_screen_has_no_shared_role_switch_or_demo_credentials(self):
        self.bootstrap()
        user = StaffProfile.objects.get().user
        user.is_active = True
        user.save(update_fields=["is_active"])
        self.client.force_login(user)
        with override_settings(PORTAL_LOCAL=False):
            response = self.client.get(reverse("staff:dashboard"))
            self.assertNotContains(response, "PRIVATE REVIEW")
            self.assertNotContains(response, "Explore the workflow")
            self.assertNotContains(response, "Demo may reset")
            self.assertEqual(self.client.post(reverse("staff:switch_role"), {"role": "finance"}).status_code, 404)

    @override_settings(PORTAL_CAPTCHA_MODE="simulate")
    def test_old_demo_password_and_session_do_not_grant_personal_account_access(self):
        user = get_user_model().objects.create_user("old-demo", "demo@example.test", "old-shared-code")
        StaffProfile.objects.create(user=user, name="Old demo", email="demo@example.test", job_title="Test", is_demo=True)
        self.client.post(reverse("staff:login"), {"email": "demo@example.test", "password": "old-shared-code", "captcha": "pass"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse("staff:dashboard")), reverse("staff:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class ProductionConfigurationTests(SimpleTestCase):
    def test_production_requires_permanent_storage_real_captcha_and_email_mode(self):
        env = {**os.environ, "APP_ENV": "production", "DJANGO_SECRET_KEY": "test-only-" * 9,
               "RENDER_EXTERNAL_HOSTNAME": "staff.example.org", "DATABASE_URL": "postgresql://test:test@localhost/staff",
               "MAILJET_API_KEY": "fake-key", "MAILJET_API_SECRET": "fake-secret", "DEFAULT_FROM_EMAIL": "sender@example.org",
               "TIMESHEET_EMAIL_DELIVERY": "mailjet", "TIMESHEET_MAILJET_API_KEY": "fake-key",
               "TIMESHEET_MAILJET_API_SECRET": "fake-secret", "TIMESHEET_FROM_EMAIL": "sender@example.org",
               "TIMESHEET_NOTIFICATION_EMAIL": "reviewer@example.org", "TURNSTILE_SITE_KEY": "site-key-from-real-widget",
               "TURNSTILE_SECRET_KEY": "fake-secret", "TURNSTILE_HOSTNAMES": "staff.example.org"}
        code = ("from config import settings as s; assert not s.PORTAL_PREVIEW and not s.DEBUG; "
                "assert s.PORTAL_CAPTCHA_MODE == 'turnstile'; assert s.SESSION_COOKIE_SECURE")
        result = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0)
        for key, value in [("DATABASE_URL", "sqlite:///:memory:"), ("TURNSTILE_SECRET_KEY", ""),
                           ("TURNSTILE_SITE_KEY", "1x00000000000000000000AA"), ("TIMESHEET_EMAIL_DELIVERY", "preview")]:
            with self.subTest(setting=key):
                result = subprocess.run([sys.executable, "-c", "from config import settings"],
                                        cwd=settings.BASE_DIR, env={**env, key: value}, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
