import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import StaffProfile, SubmissionNotification, Timesheet


CODE = "fictional-test-review-code-2026-only"


@override_settings(PORTAL_PREVIEW=True, PREVIEW_ACCESS_CODE=CODE,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PrivatePreviewTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = override_settings(EMAIL_FILE_PATH=Path(self.directory.name))
        self.config.enable()
        self.addCleanup(self.config.disable)
        call_command("bootstrap_preview", stdout=io.StringIO())

    def sign_in(self, role="employee", code=CODE):
        return self.client.post(reverse("staff:login"), {"role": role, "access_code": code})

    def test_code_is_required_and_never_in_page_source(self):
        response = self.client.get(reverse("staff:login"))
        self.assertContains(response, "Private review code")
        self.assertNotContains(response, CODE)
        self.assertNotContains(response, "data-demo-password")
        self.assertEqual(self.sign_in(code="wrong").status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        sheet = Timesheet.objects.first()
        self.assertRedirects(self.client.get(reverse("staff:sheet", args=[sheet.pk])), reverse("staff:login"))

    def test_demo_login_and_role_switch_work_without_an_extra_password(self):
        self.assertEqual(self.sign_in().status_code, 302)
        self.assertEqual(self.client.get(reverse("staff:reports")).status_code, 403)
        response = self.client.post(reverse("staff:switch_role"), {"role": "finance"})
        self.assertRedirects(response, reverse("staff:reports"))
        self.assertEqual(self.client.get(reverse("staff:reports")).status_code, 200)
        self.assertEqual(self.client.get(reverse("staff:switch_role")).status_code, 405)

    def test_role_switch_requires_authenticated_demo_user_and_csrf(self):
        self.assertEqual(self.client.post(reverse("staff:switch_role"), {"role": "finance"}).status_code, 403)
        user = StaffProfile.objects.get(role="employee").user
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        self.assertEqual(client.post(reverse("staff:switch_role"), {"role": "finance"}).status_code, 403)

    def test_shared_demo_cannot_invite_or_change_accounts(self):
        self.sign_in("administrator")
        employee = StaffProfile.objects.get(role="employee")
        for name, args in [("staff:staff_add", []), ("staff:staff_edit", [employee.pk]),
                           ("staff:invite", [employee.pk]), ("staff:security", []),
                           ("staff:reset_password", []), ("staff:set_password", [])]:
            for method in (self.client.get, self.client.post):
                with self.subTest(route=name):
                    self.assertEqual(method(reverse(name, args=args)).status_code, 403)
        self.assertEqual(StaffProfile.objects.count(), 3)

    def test_role_switch_is_not_available_in_live_payroll(self):
        self.sign_in()
        with override_settings(PORTAL_PREVIEW=False):
            self.assertEqual(self.client.post(reverse("staff:switch_role"), {"role": "administrator"}).status_code, 404)

    def test_seeding_is_idempotent_and_only_fictional(self):
        before = Timesheet.objects.count()
        call_command("bootstrap_preview", stdout=io.StringIO())
        self.assertEqual(Timesheet.objects.count(), before)
        self.assertEqual(StaffProfile.objects.count(), 3)
        self.assertFalse(StaffProfile.objects.filter(is_demo=False).exists())
        self.assertTrue(all(p.email.endswith("@example.test") for p in StaffProfile.objects.all()))
        with override_settings(PORTAL_PREVIEW=False), self.assertRaises(CommandError):
            call_command("bootstrap_preview", stdout=io.StringIO())
        StaffProfile.objects.create(name="Real person must not be imported", job_title="Test", is_demo=False)
        with self.assertRaises(CommandError):
            call_command("bootstrap_preview", stdout=io.StringIO())

    def test_preview_login_is_rate_limited(self):
        for _ in range(10):
            self.sign_in(code="incorrect")
        response = self.sign_in()
        self.assertContains(response, "Too many attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_hosted_preview_keeps_secure_cookies_and_private_headers(self):
        with override_settings(PORTAL_LOCAL=False, DEBUG=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True):
            response = self.client.get(reverse("staff:login"), secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.cookies["csrftoken"]["secure"])
            self.assertIn("no-store", response["Cache-Control"])
            self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
            response = self.client.post(reverse("staff:login"), {"role": "employee", "access_code": CODE}, secure=True)
            self.assertTrue(response.cookies["sessionid"]["secure"])

    def test_health_is_available_and_has_no_staff_data(self):
        response = self.client.get("/health/")
        self.assertEqual(response.json(), {"status": "ok"})

    def test_preview_ignores_any_inherited_live_mailjet_configuration(self):
        env = {**os.environ, "APP_ENV": "preview", "DJANGO_SECRET_KEY": "x" * 64,
               "PREVIEW_ACCESS_CODE": CODE, "RENDER_EXTERNAL_HOSTNAME": "preview.example.test",
               # Keep this fixture independent of a developer's explicit claim-mail opt-in.
               "TIMESHEET_EMAIL_DELIVERY": "preview",
               "MAILJET_API_KEY": "must-not-be-used", "MAILJET_API_SECRET": "must-not-be-used"}
        code = ("from config import settings as s; "
                "assert s.MAILJET_API_KEY == s.MAILJET_API_SECRET == ''; "
                "assert s.TIMESHEET_EMAIL_DELIVERY == 'preview'; "
                "assert s.EMAIL_BACKEND == 'django.core.mail.backends.filebased.EmailBackend'; "
                "assert s.DEBUG is False; assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE")
        result = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_explicit_claim_email_configuration_keeps_account_email_isolated(self):
        env = {**os.environ, "APP_ENV": "preview", "DJANGO_SECRET_KEY": "x" * 64,
               "PREVIEW_ACCESS_CODE": CODE, "RENDER_EXTERNAL_HOSTNAME": "preview.example.test",
               "TIMESHEET_EMAIL_DELIVERY": "mailjet", "TIMESHEET_MAILJET_API_KEY": "fake-test-key",
               "TIMESHEET_MAILJET_API_SECRET": "fake-test-secret",
               "TIMESHEET_FROM_EMAIL": "sender@example.org", "TIMESHEET_NOTIFICATION_EMAIL": "reviewer@example.org"}
        code = ("from config import settings as s; "
                "assert s.MAILJET_API_KEY == s.MAILJET_API_SECRET == ''; "
                "assert s.TIMESHEET_EMAIL_DELIVERY == 'mailjet'; "
                "assert s.TIMESHEET_MAILJET_API_KEY == 'fake-test-key'; "
                "assert s.TIMESHEET_NOTIFICATION_EMAIL == 'reviewer@example.org'; "
                "assert s.TIMESHEET_FROM_EMAIL == 'sender@example.org'; "
                "assert s.EMAIL_BACKEND == 'django.core.mail.backends.filebased.EmailBackend'")
        result = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0)
        for incomplete in ["reviewer@geezabreak", "", "reviewer@example.test"]:
            with self.subTest(recipient=incomplete):
                env["TIMESHEET_NOTIFICATION_EMAIL"] = incomplete
                result = subprocess.run([sys.executable, "-c", "from config import settings"],
                                        cwd=settings.BASE_DIR, env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
