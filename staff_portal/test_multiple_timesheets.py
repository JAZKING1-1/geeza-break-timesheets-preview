import csv
import datetime as dt
import uuid
from io import BytesIO, StringIO
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .bulk import RowErrors, save_month
from .excel import submission_workbook
from .feedback import confirm_non_respite
from .models import AuditEvent, StaffProfile, TimeEntry, Timesheet, WorkCategory
from .services import create_timesheet, save_entry, transition
from .test_excel_notifications import workbook_cells


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, TIMESHEET_EMAIL_DELIVERY="preview",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MultipleTimesheetTests(TestCase):
    def setUp(self):
        self.people = {}
        for name, role in (("Alex", "employee"), ("Finley", "finance"), ("Other", "employee")):
            user = get_user_model().objects.create_user(name, f"{name.lower()}@example.test", "fictional-password")
            self.people[name] = StaffProfile.objects.create(user=user, name=name, email=user.email, role=role)
        self.staff = self.people["Alex"]
        self.category = WorkCategory.objects.create(name="Office work")
        self.first = self.new(5)
        self.second = self.new(12)

    def new(self, day):
        return create_timesheet(self.staff.user, dt.date(2026, 10, day), uuid.uuid4())

    def shift(self, sheet, day):
        entry = TimeEntry(timesheet=sheet, date=dt.date(2026, 10, day), end_date=dt.date(2026, 10, day),
                          start_time=dt.time(9), end_time=dt.time(11), break_minutes=0, category=self.category)
        save_entry(self.staff.user, sheet.pk, sheet.revision, entry)
        sheet.refresh_from_db()
        confirm_non_respite(sheet)
        return entry

    def test_month_totals_filters_csv_and_zip_include_every_week(self):
        self.shift(self.first, 5)
        self.shift(self.second, 12)
        self.first = transition(self.staff.user, self.first.pk, self.first.revision, "submit", notify=False)
        self.first = transition(self.people["Finley"].user, self.first.pk, self.first.revision, "approve")
        self.new(19)  # A second draft must not count Alex twice in chasing.
        self.client.force_login(self.people["Finley"].user)
        query = {"month": "2026-10", "employee": self.staff.pk}
        response = self.client.get(reverse("staff:reports"), query)
        self.assertEqual(response.context["sheet_count"], 3)
        self.assertEqual(response.context["employee_count"], 1)
        self.assertEqual(response.context["totals"]["chasing"], 1)
        self.assertEqual(response.context["approved_hours"], "2.00")
        self.assertEqual(response.context["pending_hours"], "2.00")
        filtered = self.client.get(reverse("staff:reports"), {**query, "status": "approved"})
        self.assertEqual([r["sheet"].pk for r in filtered.context["rows"]], [self.first.pk])
        for detail in ("0", "1"):
            response = self.client.get(reverse("staff:export_report"), {**query, "detail": detail})
            rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
            self.assertIn(self.first.reference, [r["Reference"] for r in rows])
            self.assertIn(self.second.reference, [r["Reference"] for r in rows])
        response = self.client.get(reverse("staff:export_workbooks"), {**query, "format": "zip"})
        with ZipFile(BytesIO(response.content)) as archive:
            names = [n for n in archive.namelist() if n.startswith("timesheet-")]
            self.assertEqual(len(names), 3)
            self.assertEqual(len(set(names)), 3)
            cells, _, _ = workbook_cells(archive.read("geezabreak-2026-10-team.xlsx"))
            references = [cell["value"] for key, cell in cells.items() if key.startswith("I")]
            self.assertTrue(any(self.first.reference in str(v) for v in references))
            self.assertTrue(any(self.second.reference in str(v) for v in references))
        self.client.force_login(self.staff.user)
        dashboard = self.client.get(reverse("staff:dashboard"), {"month": "2026-10"})
        self.assertEqual(dashboard.context["month_total"], "4h 00m")
        self.assertEqual(dashboard.context["month_count"], 3)

    def test_overlaps_between_weekly_claims_rejected_in_single_and_bulk_edit(self):
        self.shift(self.first, 5)
        with self.assertRaises(ValidationError):
            self.shift(self.second, 5)
        with self.assertRaises(RowErrors):
            save_month(self.staff.user, self.second.pk, 0, [{
                "date": dt.date(2026, 10, 5), "end_date": dt.date(2026, 10, 5),
                "start_time": dt.time(10), "end_time": dt.time(12), "break_minutes": 0,
                "category": self.category, "notes": "", "start_fold": 0, "end_fold": 0,
            }], submit=True)
        self.second.refresh_from_db()
        self.assertEqual(self.second.revision, 0)
        self.assertFalse(self.second.entries.exists())
        self.assertFalse(self.second.notifications.exists())
        self.assertEqual(self.first.entries.count(), 1)

    def test_creation_tokens_are_required_isolated_and_cannot_change_month(self):
        self.client.force_login(self.staff.user)
        url = reverse("staff:new_sheet")
        self.assertEqual(self.client.post(url, {"work_date": "2026-10-19"}).status_code, 400)
        token = str(uuid.uuid4())
        data = {"work_date": "2026-10-19", "creation_token": token}
        first = self.client.post(url, data)
        self.assertEqual(first.url, self.client.post(url, data).url)
        self.assertEqual(self.client.post(url, {**data, "work_date": "2026-11-19"}).status_code, 409)
        self.client.force_login(self.people["Other"].user)
        other = self.client.post(url, data)
        self.assertNotEqual(other.url, first.url)
        self.assertEqual(self.client.get(first.url).status_code, 404)
        self.assertEqual(Timesheet.objects.filter(staff=self.staff).count(), 3)

    def test_email_attachment_names_are_unique_for_same_month_and_revision(self):
        self.assertNotEqual(submission_workbook(self.first)[0], submission_workbook(self.second)[0])

    def test_roster_audit_is_read_only_and_contains_no_credentials_or_emails(self):
        output = StringIO()
        before = (StaffProfile.objects.count(), get_user_model().objects.count(), AuditEvent.objects.count())
        call_command("audit_staff", stdout=output)
        self.assertIn('"name": "Alex"', output.getvalue())
        self.assertIn('"role": "finance"', output.getvalue())
        self.assertNotIn("@example.test", output.getvalue())
        self.assertNotIn(self.staff.user.password, output.getvalue())
        self.assertEqual(before, (StaffProfile.objects.count(), get_user_model().objects.count(), AuditEvent.objects.count()))


class MultipleTimesheetMigrationTests(TransactionTestCase):
    def test_legacy_approved_record_and_history_survive_upgrade(self):
        old = [("staff_portal", "0004_submission_excel_snapshot")]
        new = [("staff_portal", "0006_sessionfeedback")]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            profile = apps.get_model("staff_portal", "StaffProfile").objects.create(name="Legacy example", job_title="Support")
            sheet = apps.get_model("staff_portal", "Timesheet").objects.create(
                staff=profile, month=dt.date(2026, 9, 1), status="approved", revision=4)
            event = apps.get_model("staff_portal", "AuditEvent").objects.create(
                staff=profile, timesheet=sheet, action="Approved", detail={"total_minutes": 120})
        finally:
            MigrationExecutor(connection).migrate(new)
        preserved = Timesheet.objects.get(pk=sheet.pk)
        self.assertEqual((preserved.status, preserved.revision, preserved.staff_id), ("approved", 4, profile.pk))
        self.assertIsNone(preserved.creation_token)
        self.assertEqual(AuditEvent.objects.get(pk=event.pk).detail, {"total_minutes": 120})
        Timesheet.objects.create(staff_id=profile.pk, month=preserved.month)
        self.assertEqual(Timesheet.objects.filter(staff_id=profile.pk, month=preserved.month).count(), 2)
