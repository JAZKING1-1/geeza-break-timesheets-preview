import datetime as dt
import io
import re
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from .accounts import consume_link, issue_link, new_recovery_codes, verify_second_factor
from .models import AuditEvent, PasswordLink, StaffProfile, TimeEntry, Timesheet, WorkCategory
from .security import token_digest
from .services import EditConflict, delete_entry, save_entry, transition

PASSWORD = "Four friendly gardens shine!"


@override_settings(PORTAL_PREVIEW=False, PORTAL_CAPTCHA_MODE="simulate", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PortalTests(TestCase):
    def setUp(self):
        self.people = {}
        for name, role in [("employee", "employee"), ("other", "employee"),
                           ("finance", "finance"), ("admin", "administrator")]:
            user = get_user_model().objects.create_user(name, f"{name}@example.test", PASSWORD)
            self.people[name] = StaffProfile.objects.create(user=user, name=name.title(),
                job_title="Test job", email=user.email, role=role)
        self.employee = self.people["employee"]
        self.finance = self.people["finance"]
        self.admin = self.people["admin"]
        self.category = WorkCategory.objects.create(name="Family Support")
        self.sheet = Timesheet.objects.create(staff=self.employee, month=dt.date(2026, 9, 1))

    def entry(self, sheet=None, **kwargs):
        sheet = sheet or self.sheet
        values = dict(timesheet=sheet, date=sheet.month, end_date=sheet.month, start_time=dt.time(9),
                      end_time=dt.time(17), break_minutes=30, category=self.category)
        values.update(kwargs)
        return TimeEntry(**values)

    def add(self, **kwargs):
        self.sheet.refresh_from_db()
        return save_entry(self.employee.user, self.sheet.pk, self.sheet.revision, self.entry(**kwargs))

    def submit(self):
        self.add()
        self.sheet.refresh_from_db()
        return transition(self.employee.user, self.sheet.pk, self.sheet.revision, "submit")

    def login(self, person="employee"):
        self.client.force_login(self.people[person].user)

    def test_shift_minutes_and_total_deduct_break(self):
        item = self.add()
        self.assertEqual(item.minutes, 450)
        self.assertEqual(self.sheet.total_display, "7h 30m")

    def test_overlapping_shifts_rejected_without_revision_change(self):
        self.add()
        with self.assertRaises(ValidationError):
            self.add(start_time=dt.time(16), end_time=dt.time(19))
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.revision, 1)
        self.assertEqual(self.sheet.entries.count(), 1)

    def test_adjacent_shifts_permitted(self):
        self.add()
        self.add(start_time=dt.time(17), end_time=dt.time(19), break_minutes=0)
        self.assertEqual(self.sheet.total_minutes, 570)

    def test_invalid_duration_and_break(self):
        for options in [dict(end_time=dt.time(8)), dict(break_minutes=480), dict(break_minutes=-1)]:
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.add(**options)

    def test_overnight_shift(self):
        item = self.add(start_time=dt.time(22), end_date=dt.date(2026, 9, 2), end_time=dt.time(6))
        self.assertEqual(item.minutes, 450)

    def test_month_boundary_must_be_split_but_midnight_allowed(self):
        with self.assertRaises(ValidationError):
            self.add(date=dt.date(2026, 9, 30), start_time=dt.time(22),
                     end_date=dt.date(2026, 10, 1), end_time=dt.time(2))
        item = self.add(date=dt.date(2026, 9, 30), start_time=dt.time(22),
                        end_date=dt.date(2026, 10, 1), end_time=dt.time(0), break_minutes=0)
        self.assertEqual(item.minutes, 120)

    def test_spring_clock_change_and_nonexistent_time(self):
        sheet = Timesheet.objects.create(staff=self.employee, month=dt.date(2026, 3, 1))
        item = self.entry(sheet, date=dt.date(2026, 3, 29), end_date=dt.date(2026, 3, 29),
                          start_time=dt.time(0, 30), end_time=dt.time(3, 30), break_minutes=0)
        save_entry(self.employee.user, sheet.pk, 0, item)
        self.assertEqual(item.minutes, 120)
        with self.assertRaises(ValidationError):
            self.entry(sheet, date=dt.date(2026, 3, 29), end_date=dt.date(2026, 3, 29),
                       start_time=dt.time(1, 30), end_time=dt.time(4)).clean()

    def test_autumn_repeat_time_and_leap_year(self):
        sheet = Timesheet.objects.create(staff=self.employee, month=dt.date(2026, 10, 1))
        item = self.entry(sheet, date=dt.date(2026, 10, 25), end_date=dt.date(2026, 10, 25),
                          start_time=dt.time(1, 30), end_time=dt.time(1, 30),
                          start_fold=0, end_fold=1, break_minutes=0)
        save_entry(self.employee.user, sheet.pk, 0, item)
        self.assertEqual(item.minutes, 60)
        leap = Timesheet.objects.create(staff=self.employee, month=dt.date(2028, 2, 1))
        item = self.entry(leap, date=dt.date(2028, 2, 29), end_date=dt.date(2028, 2, 29))
        save_entry(self.employee.user, leap.pk, 0, item)
        self.assertEqual(item.minutes, 450)

    def test_duplicate_month_database_constraint(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Timesheet.objects.create(staff=self.employee, month=self.sheet.month)

    def test_stale_revision_cannot_change_sheet(self):
        first = self.add()
        with self.assertRaises(EditConflict):
            delete_entry(self.employee.user, self.sheet.pk, 0, first.pk)
        self.assertTrue(TimeEntry.objects.filter(pk=first.pk).exists())

    def test_submission_locks_and_repeated_submission_rejected(self):
        submitted = self.submit()
        with self.assertRaises(PermissionDenied):
            save_entry(self.employee.user, self.sheet.pk, submitted.revision, self.entry(date=dt.date(2026, 9, 2)))
        with self.assertRaises(PermissionDenied):
            transition(self.employee.user, self.sheet.pk, submitted.revision, "submit")

    def test_no_empty_submission(self):
        with self.assertRaises(ValidationError):
            transition(self.employee.user, self.sheet.pk, 0, "submit")

    def test_return_correct_resubmit_approve_reopen(self):
        sheet = self.submit()
        with self.assertRaises(ValidationError):
            transition(self.finance.user, sheet.pk, sheet.revision, "return")
        sheet = transition(self.finance.user, sheet.pk, sheet.revision, "return", "Check the break.")
        item = sheet.entries.first()
        item.break_minutes = 60
        save_entry(self.employee.user, sheet.pk, sheet.revision, item)
        sheet.refresh_from_db()
        sheet = transition(self.employee.user, sheet.pk, sheet.revision, "submit")
        sheet = transition(self.finance.user, sheet.pk, sheet.revision, "approve")
        self.assertEqual(sheet.total_minutes, 420)
        with self.assertRaises(ValidationError):
            transition(self.finance.user, sheet.pk, sheet.revision, "reopen")
        sheet = transition(self.finance.user, sheet.pk, sheet.revision, "reopen", "Correction requested.")
        self.assertEqual(sheet.status, "returned")
        self.assertIsNone(sheet.approved_at)
        self.assertEqual(sheet.events.filter(action="Entry updated").first().detail["before"]["break_minutes"], "30")

    def test_reviewer_cannot_approve_self(self):
        sheet = Timesheet.objects.create(staff=self.finance, month=self.sheet.month, status="submitted")
        with self.assertRaises(PermissionDenied):
            transition(self.finance.user, sheet.pk, 0, "approve")
        self.assertEqual(transition(self.admin.user, sheet.pk, 0, "approve").status, "approved")

    def test_other_employee_cannot_edit_or_review(self):
        self.add()
        for action in ("submit", "approve", "return", "reopen"):
            with self.subTest(action=action), self.assertRaises(PermissionDenied):
                transition(self.people["other"].user, self.sheet.pk, 1, action, "A reason")

    def test_foreign_entry_id_rejected(self):
        another = Timesheet.objects.create(staff=self.people["other"], month=self.sheet.month)
        item = self.entry(another)
        save_entry(self.people["other"].user, another.pk, 0, item)
        with self.assertRaises(PermissionDenied):
            save_entry(self.employee.user, self.sheet.pk, 0, item)

    def test_anonymous_redirect_and_record_isolation(self):
        url = reverse("staff:sheet", args=[self.sheet.pk])
        self.assertRedirects(self.client.get(url), reverse("staff:login"))
        self.login("other")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(reverse("staff:sheet_action", args=[self.sheet.pk]),
                                         {"action": "submit", "revision": 0}).status_code, 404)
        self.assertEqual(self.client.get(reverse("staff:reports")).status_code, 403)
        self.assertEqual(self.client.get(reverse("staff:export_report")).status_code, 403)
        self.assertEqual(self.client.get(reverse("staff:staff_list")).status_code, 403)

    def test_finance_cannot_manage_staff_or_referrals(self):
        self.login("finance")
        self.assertEqual(self.client.get(reverse("staff:staff_list")).status_code, 403)

    def test_page_rendering_and_security_headers(self):
        self.add()
        for person, urls in [
            ("employee", ["staff:dashboard", "staff:security"]),
            ("finance", ["staff:reports"]),
            ("admin", ["staff:staff_list", "staff:staff_add", "staff:local_emails"]),
        ]:
            self.login(person)
            for name in urls:
                with self.subTest(name=name):
                    response = self.client.get(reverse(name))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.login()
        for name, args in [("staff:sheet", [self.sheet.pk]),
                           ("staff:entry_edit", [self.sheet.pk, self.sheet.entries.first().pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_entry_form_post_and_invalid_dates(self):
        self.login()
        url = reverse("staff:entry_add", args=[self.sheet.pk])
        data = {"revision": 0, "date": "2026-09-01", "end_date": "2026-09-01",
                "start_time": "09:00", "end_time": "17:00", "break_minutes": 30,
                "category": self.category.pk, "start_fold": 0, "end_fold": 0, "notes": "A shift"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.sheet.entries.get().minutes, 450)
        data["date"] = "not-a-date"
        self.assertContains(self.client.post(url, data), "Please check the following")

    def test_saved_form_dates_render_as_iso(self):
        item = self.add()
        self.login()
        response = self.client.get(reverse("staff:entry_edit", args=[self.sheet.pk, item.pk]))
        self.assertContains(response, 'value="2026-09-01"')

    def test_report_export_matches_approved_pending_and_disabled_history(self):
        self.submit()
        self.sheet.refresh_from_db()
        transition(self.finance.user, self.sheet.pk, self.sheet.revision, "approve")
        self.employee.enabled = False
        self.employee.save()
        self.login("finance")
        response = self.client.get(reverse("staff:reports"), {"month": "2026-09"})
        self.assertContains(response, "7.50")
        self.assertContains(response, "Disabled account")
        export = self.client.get(reverse("staff:export_report"), {"month": "2026-09"})
        self.assertIn("Employee,Test job,Approved,450,7.50,7.50,0.00", export.content.decode("utf-8-sig"))
        self.assertTrue(AuditEvent.objects.filter(action="Report exported").exists())

    def test_reopen_removes_hours_from_approved_report(self):
        item = self.submit()
        item = transition(self.finance.user, item.pk, item.revision, "approve")
        transition(self.finance.user, item.pk, item.revision, "reopen", "Fix hours")
        self.login("finance")
        response = self.client.get(reverse("staff:reports"), {"month": "2026-09"})
        self.assertEqual(response.context["approved_hours"], "0.00")
        self.assertEqual(response.context["pending_hours"], "7.50")

    def test_csv_injection_and_detail(self):
        self.add(notes=" =HYPERLINK(\"evil\")")
        self.login("finance")
        response = self.client.get(reverse("staff:export_report"), {"month": "2026-09", "detail": 1})
        self.assertIn("' =HYPERLINK", response.content.decode("utf-8-sig"))
        self.assertIn("Family Support", response.content.decode())

    def test_disabled_account_loses_existing_session(self):
        self.login()
        self.employee.enabled = False
        self.employee.save()
        self.assertRedirects(self.client.get(reverse("staff:dashboard")), reverse("staff:login"))

    def test_idle_session_expires(self):
        self.login()
        session = self.client.session
        session["portal_last_activity"] = timezone.now().timestamp() - 1900
        session.save()
        self.assertRedirects(self.client.get(reverse("staff:dashboard")), reverse("staff:login"))

    def test_csrf_required_and_mutating_get_denied(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee.user)
        self.assertEqual(client.post(reverse("staff:new_sheet"), {"month": "2026-10"}).status_code, 403)
        self.assertEqual(client.get(reverse("staff:logout")).status_code, 405)

    def test_login_keeps_same_origin_referrer_and_enforces_csrf(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get(reverse("staff:login"))
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        token = client.cookies["csrftoken"].value
        data = {"email": self.employee.email, "password": PASSWORD, "captcha": "pass",
                "csrfmiddlewaretoken": token}
        self.assertEqual(client.post(reverse("staff:login"), data, HTTP_ORIGIN="https://attacker.example").status_code, 403)
        self.assertEqual(client.post(reverse("staff:login"), data, HTTP_ORIGIN="null").status_code, 403)
        self.assertRedirects(client.post(reverse("staff:login"), data, HTTP_ORIGIN="http://testserver"),
                             reverse("staff:dashboard"))

    def test_one_use_link_redirect_still_hides_token_referrer(self):
        link = issue_link(self.employee, "reset")
        token = re.search(r"/set-password/([^/]+)/", mail.outbox[-1].body).group(1)
        response = self.client.get(reverse("staff:password_link", args=[token]))
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        page = self.client.get(reverse("staff:set_password"))
        self.assertEqual(page["Referrer-Policy"], "same-origin")

    def test_login_and_failed_captcha(self):
        response = self.client.post(reverse("staff:login"), {"email": self.employee.email,
            "password": PASSWORD, "captcha": "fail"})
        self.assertContains(response, "Verification was unsuccessful")
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.client.post(reverse("staff:login"), {"email": self.employee.email.upper(),
            "password": PASSWORD, "captcha": "pass"})
        self.assertRedirects(response, reverse("staff:dashboard"))

    def test_login_rate_limit(self):
        for _ in range(11):
            response = self.client.post(reverse("staff:login"), {"email": self.employee.email,
                "password": "wrong", "captcha": "pass"})
        self.assertContains(response, "Too many attempts")

    def test_invitation_only_recipient_one_use_and_revocation(self):
        profile = StaffProfile.objects.create(name="New employee", job_title="Support", email="new@example.test")
        first = issue_link(profile, "setup", self.admin.user)
        self.assertEqual(mail.outbox[-1].to, ["new@example.test"])
        self.assertEqual(mail.outbox[-1].cc, [])
        profile.refresh_from_db()
        self.assertFalse(profile.user.is_active)
        second = issue_link(profile, "setup", self.admin.user)
        first.refresh_from_db()
        self.assertIsNotNone(first.used_at)
        token = re.search(r"/set-password/([^/]+)/", mail.outbox[-1].body).group(1)
        self.assertNotEqual(token, second.token_hash)
        self.assertRedirects(self.client.get(reverse("staff:password_link", args=[token])), reverse("staff:set_password"))
        response = self.client.post(reverse("staff:set_password"), {"new_password1": PASSWORD, "new_password2": PASSWORD})
        self.assertRedirects(response, reverse("staff:login"))
        profile.user.refresh_from_db()
        self.assertTrue(profile.user.is_active)
        self.assertTrue(profile.user.check_password(PASSWORD))
        self.assertEqual(self.client.get(reverse("staff:password_link", args=[token])).status_code, 400)

    def test_expired_link_and_weak_password(self):
        link = issue_link(self.employee, "reset")
        with self.assertRaises(ValidationError):
            consume_link(link.pk, link.token_hash, "short")
        link.refresh_from_db()
        self.assertIsNone(link.used_at)
        link.expires_at = timezone.now() - dt.timedelta(seconds=1)
        link.save()
        with self.assertRaises(ValidationError):
            consume_link(link.pk, link.token_hash, PASSWORD)

    def test_reset_generic_response_and_recipient_only(self):
        for address in [self.employee.email, "unknown@example.test"]:
            response = self.client.post(reverse("staff:reset_password"), {"email": address, "captcha": "pass"})
            self.assertContains(response, "If this address belongs to an active account")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.employee.email])

    def test_email_change_revokes_links_and_requires_setup_again(self):
        link = issue_link(self.employee, "reset")
        self.login("admin")
        response = self.client.post(reverse("staff:staff_edit", args=[self.employee.pk]),
            {"name": "Employee", "job_title": "Support", "email": "replacement@example.test", "role": "employee", "enabled": "on"})
        self.assertEqual(response.status_code, 302)
        self.employee.user.refresh_from_db()
        self.assertFalse(self.employee.user.is_active)
        self.assertFalse(self.employee.user.has_usable_password())
        link.refresh_from_db()
        self.assertIsNotNone(link.used_at)

    def test_email_case_insensitive_uniqueness(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            StaffProfile.objects.create(name="Duplicate", job_title="Support", email=self.employee.email.upper())

    def test_admin_cannot_disable_self(self):
        self.login("admin")
        response = self.client.post(reverse("staff:staff_edit", args=[self.admin.pk]),
            {"name": "Admin", "job_title": "CEO", "email": self.admin.email, "role": "employee"})
        self.assertContains(response, "You cannot disable")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.enabled)



    def test_local_emails_not_available_outside_local_mode(self):
        self.login("admin")
        with override_settings(PORTAL_LOCAL=False):
            self.assertEqual(self.client.get(reverse("staff:local_emails")).status_code, 404)

    def test_mfa_login_requires_code_and_recovery_is_one_use(self):
        TOTPDevice.objects.create(user=self.employee.user, name="Authenticator", confirmed=True)
        codes = new_recovery_codes(self.employee.user)
        response = self.client.post(reverse("staff:login"),
            {"email": self.employee.email, "password": PASSWORD, "captcha": "pass"})
        self.assertEqual(response.url, reverse("staff:second_factor"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.post(reverse("staff:second_factor"), {"code": codes[0]}),
                             reverse("staff:dashboard"))
        self.assertFalse(verify_second_factor(self.employee.user, codes[0]))

    def test_authenticator_enrollment_and_replayed_code(self):
        self.login()
        response = self.client.post(reverse("staff:security"), {"action": "begin", "password": PASSWORD})
        self.assertContains(response, "Scan this QR code")
        device = TOTPDevice.objects.get(user=self.employee.user)
        code = str(totp(device.bin_key)).zfill(6)
        response = self.client.post(reverse("staff:security"),
                                   {"action": "confirm", "password": PASSWORD, "code": code})
        self.assertContains(response, "Save your recovery codes now")
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assertFalse(verify_second_factor(self.employee.user, code))

    def test_password_reset_preserves_mfa(self):
        device = TOTPDevice.objects.create(user=self.employee.user, confirmed=True)
        link = issue_link(self.employee, "reset")
        consume_link(link.pk, link.token_hash, PASSWORD + " another")
        self.assertTrue(TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists())

    def test_turnstile_validates_hostname_action_and_errors(self):
        with override_settings(PORTAL_CAPTCHA_MODE="turnstile", TURNSTILE_SECRET_KEY="test-secret",
                               TURNSTILE_HOSTNAMES=["staff.example.test"]):
            with patch("staff_portal.security.requests.post") as post:
                post.return_value = Mock()
                post.return_value.json.return_value = {"success": True, "action": "login", "hostname": "wrong.test"}
                response = self.client.post(reverse("staff:login"), {"email": self.employee.email,
                    "password": PASSWORD, "cf-turnstile-response": "fake"})
                self.assertContains(response, "Verification was unsuccessful")
                post.return_value.json.return_value = {"success": True, "action": "login", "hostname": "staff.example.test"}
                response = self.client.post(reverse("staff:login"), {"email": self.employee.email,
                    "password": PASSWORD, "cf-turnstile-response": "fake"})
                self.assertEqual(response.status_code, 302)

    def test_notes_are_escaped_on_timesheet(self):
        self.add(notes="<script>alert('test')</script>")
        self.login()
        response = self.client.get(reverse("staff:sheet", args=[self.sheet.pk]))
        self.assertNotContains(response, "<script>alert")
        self.assertContains(response, "&lt;script&gt;")

    def test_disabled_link_cannot_activate_staff(self):
        link = issue_link(self.employee, "reset")
        self.employee.enabled = False
        self.employee.save()
        with self.assertRaises(ValidationError):
            consume_link(link.pk, link.token_hash, PASSWORD)

    def test_api_tokens_redacted_from_access_log(self):
        import logging
        from .log_filters import RedactAccountLinks
        record = logging.LogRecord("django.server", logging.INFO, "", 0, "%s", (
            "GET /staff/set-password/a-private-token/ HTTP/1.1",), None)
        RedactAccountLinks().filter(record)
        self.assertNotIn("a-private-token", record.getMessage())
        self.assertIn("[redacted]", record.getMessage())

    def test_account_mail_ignores_untrusted_host_header(self):
        response = self.client.post(reverse("staff:reset_password"),
            {"email": self.employee.email, "captcha": "pass"}, HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertIn(settings.PORTAL_BASE_URL + "/staff/set-password/", mail.outbox[-1].body)



from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import OperationalError, close_old_connections
from django.test import TransactionTestCase


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ConcurrentEditTests(TransactionTestCase):
    def test_simultaneous_changes_cannot_both_save_same_revision(self):
        user = get_user_model().objects.create_user("concurrent", "concurrent@example.test", PASSWORD)
        profile = StaffProfile.objects.create(user=user, name="Concurrent demo", job_title="Support",
                                               email=user.email)
        category = WorkCategory.objects.create(name="Other")
        sheet = Timesheet.objects.create(staff=profile, month=dt.date(2026, 9, 1))
        gate = Barrier(2)

        def attempt(day):
            close_old_connections()
            try:
                local_user = get_user_model().objects.get(pk=user.pk)
                item = TimeEntry(timesheet_id=sheet.pk, date=dt.date(2026, 9, day),
                    end_date=dt.date(2026, 9, day), start_time=dt.time(9), end_time=dt.time(17),
                    break_minutes=30, category_id=category.pk)
                gate.wait(timeout=5)
                save_entry(local_user, sheet.pk, 0, item)
                return "saved"
            except (EditConflict, OperationalError):
                return "conflict"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [1, 2]))
        # SQLite may reject both simultaneous readers attempting to upgrade a lock;
        # in either case it must never apply both edits against revision zero.
        self.assertLessEqual(outcomes.count("saved"), 1)
        self.assertIn("conflict", outcomes)
        self.assertEqual(TimeEntry.objects.filter(timesheet=sheet).count(), outcomes.count("saved"))
        sheet.refresh_from_db()
        self.assertEqual(sheet.revision, outcomes.count("saved"))
