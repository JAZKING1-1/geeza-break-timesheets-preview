import uuid
import datetime as dt
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, StaffProfile, SubmissionNotification, TimeEntry, Timesheet, WorkCategory
from .payroll import payroll_dates
from .services import delete_draft, save_entry
from .test_excel_notifications import workbook_cells


class PayrollDateTests(SimpleTestCase):
    def test_every_month_uses_last_thursday_and_fourteen_calendar_days(self):
        expected_paydays = [29, 26, 26, 30, 28, 25, 30, 27, 24, 29, 26, 31]
        for month, day in enumerate(expected_paydays, 1):
            with self.subTest(month=month):
                dates = payroll_dates(dt.date(2026, month, 1), today=dt.date(2026, month, 1))
                self.assertEqual(dates["payday"], dt.date(2026, month, day))
                self.assertEqual(dates["deadline"], dt.date(2026, month, day - 14))

    def test_leap_year_and_last_day_thursday(self):
        dates = payroll_dates(dt.date(2024, 2, 1))
        self.assertEqual(dates["payday"], dt.date(2024, 2, 29))
        self.assertEqual(dates["deadline"], dt.date(2024, 2, 15))

    def test_due_date_stays_due_until_local_day_ends(self):
        for day, due_today, overdue in [(14, False, False), (15, True, False), (16, False, True)]:
            with self.subTest(day=day), patch("staff_portal.payroll.timezone.localdate", return_value=dt.date(2026, 10, day)):
                dates = payroll_dates(dt.date(2026, 10, 1))
                self.assertEqual(dates["due_today"], due_today)
                self.assertEqual(dates["overdue"], overdue)


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, TIMESHEET_EMAIL_DELIVERY="preview",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PayrollFlowTests(TestCase):
    def setUp(self):
        self.people = {}
        for name, role in [("alex", "employee"), ("other", "employee"), ("finley", "finance"), ("admin", "administrator")]:
            user = get_user_model().objects.create_user(name, f"{name}@example.test", "Fictional password")
            self.people[name] = StaffProfile.objects.create(user=user, name=name.title(), email=user.email, role=role)
        self.owner = self.people["alex"]
        self.sheet = Timesheet.objects.create(staff=self.owner, month=dt.date(2026, 10, 1))
        category = WorkCategory.objects.create(name="Family Support")
        self.entry = save_entry(self.owner.user, self.sheet.pk, 0, TimeEntry(
            timesheet=self.sheet, date=dt.date(2026, 10, 5), end_date=dt.date(2026, 10, 5),
            start_time=dt.time(9), end_time=dt.time(11), break_minutes=0, category=category, notes="Fictional test shift"))
        self.sheet.refresh_from_db()
        self.url = reverse("staff:delete_sheet", args=[self.sheet.pk])
        self.client.force_login(self.owner.user)

    def delete(self, **changes):
        return self.client.post(self.url, {"revision": self.sheet.revision, "confirm": "delete", **changes})

    def test_confirmation_does_not_delete_and_identifies_draft(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Delete this draft?")
        self.assertContains(response, "October 2026")
        self.assertContains(response, "2h 00m")
        self.assertTrue(self.sheet.entries.exists())
        self.assertEqual(self.delete(confirm="").status_code, 400)
        self.assertTrue(Timesheet.objects.filter(pk=self.sheet.pk).exists())

    def test_delete_removes_draft_entries_but_preserves_history_and_allows_new_month(self):
        event = self.sheet.events.get()
        original_detail = event.detail.copy()
        pk = self.sheet.pk
        self.assertRedirects(self.delete(), reverse("staff:dashboard"))
        self.assertFalse(Timesheet.objects.filter(pk=pk).exists())
        self.assertFalse(TimeEntry.objects.filter(pk=self.entry.pk).exists())
        event.refresh_from_db()
        self.assertIsNone(event.timesheet_id)
        self.assertEqual(event.detail["after"], original_detail["after"])
        self.assertEqual(event.detail["deleted_timesheet_id"], pk)
        self.assertEqual(event.staff, self.owner)
        deletion = AuditEvent.objects.get(action="Draft timesheet deleted")
        self.assertEqual(deletion.detail["total_minutes"], 120)
        self.assertEqual(deletion.detail["month"], "2026-10-01")
        self.assertEqual(self.client.post(reverse("staff:new_sheet"), {"work_date": "2026-10-12", "creation_token": str(uuid.uuid4())}).status_code, 302)
        self.assertNotEqual(Timesheet.objects.get(staff=self.owner, month=self.sheet.month).pk, pk)

    def test_stale_revision_cannot_delete_newer_work(self):
        self.assertEqual(self.delete(revision=0).status_code, 409)
        self.assertEqual(self.sheet.entries.count(), 1)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.revision, 1)
        self.assertFalse(AuditEvent.objects.filter(action="Draft timesheet deleted").exists())

    def test_other_staff_and_reviewers_cannot_delete_someone_elses_draft(self):
        for name in ("other", "finley", "admin"):
            with self.subTest(role=name):
                self.client.force_login(self.people[name].user)
                self.assertEqual(self.client.get(self.url).status_code, 404)
                self.assertEqual(self.delete().status_code, 404)
                with self.assertRaises(PermissionDenied):
                    delete_draft(self.people[name].user, self.sheet.pk, self.sheet.revision)
        self.assertTrue(Timesheet.objects.filter(pk=self.sheet.pk).exists())

    def test_submitted_returned_approved_and_ever_submitted_records_are_protected(self):
        for status, submitted_at in [("submitted", None), ("returned", None), ("approved", None), ("draft", timezone.now())]:
            with self.subTest(status=status):
                Timesheet.objects.filter(pk=self.sheet.pk).update(status=status, submitted_at=submitted_at)
                self.assertEqual(self.delete().status_code, 403)
                response = self.client.get(reverse("staff:sheet", args=[self.sheet.pk]))
                self.assertNotContains(response, "Delete this draft")
        self.assertTrue(self.sheet.entries.exists())

    def test_saved_submission_snapshot_prevents_deletion_even_with_invalid_draft_status(self):
        SubmissionNotification.objects.create(timesheet=self.sheet, revision=1, subject="Test", body="Test")
        self.assertEqual(self.delete().status_code, 403)
        self.assertTrue(SubmissionNotification.objects.filter(timesheet=self.sheet).exists())

    def test_delete_requires_login_and_csrf_and_repeated_post_is_safe(self):
        self.client.logout()
        self.assertEqual(self.delete().status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner.user)
        self.assertEqual(csrf_client.post(self.url, {"confirm": "delete", "revision": 1}).status_code, 403)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.delete().status_code, 302)
        self.assertEqual(self.delete().status_code, 404)

    def test_month_changes_update_dashboard_deadline_and_selected_sheet_keeps_own_deadline(self):
        for today, expected in [(dt.date(2026, 10, 31), "Thursday 15 October 2026"), (dt.date(2026, 11, 1), "Thursday 12 November 2026"), (dt.date(2027, 1, 1), "Thursday 14 January 2027")]:
            with self.subTest(today=today), patch("staff_portal.views.timezone.localdate", return_value=today):
                self.assertContains(self.client.get(reverse("staff:dashboard")), expected)
                self.assertContains(self.client.get(reverse("staff:sheet", args=[self.sheet.pk])), "Thursday 15 October 2026")

    def test_late_submission_remains_available(self):
        with patch("staff_portal.payroll.timezone.localdate", return_value=dt.date(2026, 10, 17)):
            response = self.client.get(reverse("staff:sheet", args=[self.sheet.pk]))
        self.assertContains(response, "Deadline passed")
        self.assertContains(response, 'name="action" value="submit"')

    def test_chasing_filter_includes_only_enabled_people_who_still_need_to_submit(self):
        returned = Timesheet.objects.create(staff=self.people["other"], month=self.sheet.month, status="returned")
        Timesheet.objects.create(staff=self.people["finley"], month=self.sheet.month, status="submitted")
        disabled = StaffProfile.objects.create(name="Disabled", email="disabled@example.test", enabled=False)
        Timesheet.objects.create(staff=disabled, month=self.sheet.month)
        pending = StaffProfile.objects.create(name="Pending setup", email="pending@example.test")
        self.client.force_login(self.people["finley"].user)
        query = {"month": "2026-10", "status": "needs_chasing"}
        response = self.client.get(reverse("staff:reports"), query)
        self.assertEqual({row["profile"].pk for row in response.context["rows"]}, {self.owner.pk, returned.staff_id, self.people["admin"].pk, pending.pk})
        self.assertEqual(response.context["totals"]["chasing"], 4)
        self.assertContains(response, "Thursday 15 October 2026")
        self.assertContains(response, "Email employee")
        cells, _, _ = workbook_cells(self.client.get(reverse("staff:export_workbooks"), query).content)
        exported = {cell["value"] for cell in cells.values()}
        self.assertIn("Alex", exported)
        self.assertNotIn("Finley", exported)
        self.assertNotIn("Disabled", exported)
        response = self.client.get(reverse("staff:reports"), {**query, "employee": self.owner.pk})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["profile"], self.owner)

    def test_employee_cannot_view_chasing_list_or_export_team(self):
        for name in ("reports", "export_workbooks", "export_report"):
            self.assertEqual(self.client.get(reverse(f"staff:{name}"), {"status": "needs_chasing"}).status_code, 403)
        response = self.client.get(reverse("staff:dashboard"))
        self.assertNotContains(response, "Who still needs to submit?")

    def test_finance_and_admin_can_use_team_view_but_only_admin_manages_access(self):
        for name in ("finley", "admin"):
            self.client.force_login(self.people[name].user)
            self.assertEqual(self.client.get(reverse("staff:reports"), {"month": "2026-10"}).status_code, 200)
            self.assertEqual(self.client.get(reverse("staff:staff_list")).status_code, 200 if name == "admin" else 403)
