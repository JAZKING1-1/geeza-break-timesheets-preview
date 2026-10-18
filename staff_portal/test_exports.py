import datetime as dt
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .excel import XLSX_CONTENT_TYPE
from .feedback import confirm_non_respite
from .models import AuditEvent, StaffProfile, TimeEntry, Timesheet, WorkCategory
from .services import save_entry, transition
from .test_excel_notifications import workbook_cells


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DownloadTests(TestCase):
    def setUp(self):
        self.people = {}
        for name, role in (("alex", "employee"), ("sam", "employee"), ("karen", "finance"), ("owner", "administrator")):
            user = get_user_model().objects.create_user(name, f"{name}@example.test", "local-test-password")
            self.people[name] = StaffProfile.objects.create(user=user, name=name.title(), role=role, email=user.email)
        self.category = WorkCategory.objects.create(name="Family Support")
        self.sheet = Timesheet.objects.create(staff=self.people["alex"], month=dt.date(2026, 9, 1))
        self.url = reverse("staff:export_workbooks")
        self.query = {"month": "2026-09"}

    def login(self, name="karen"):
        self.client.force_login(self.people[name].user)

    def add_shift(self, sheet=None, **kwargs):
        sheet = sheet or self.sheet
        entry = TimeEntry(timesheet=sheet, date=sheet.month, end_date=sheet.month,
                          start_time=dt.time(9), end_time=dt.time(17), break_minutes=30,
                          category=self.category, **kwargs)
        save_entry(sheet.staff.user, sheet.pk, sheet.revision, entry)
        sheet.refresh_from_db()
        confirm_non_respite(sheet)
        return entry

    def approve(self, sheet):
        sheet = transition(sheet.staff.user, sheet.pk, sheet.revision, "submit", notify=False)
        return transition(self.people["karen"].user, sheet.pk, sheet.revision, "approve")

    def test_downloads_require_sign_in_and_review_role(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.login("sam")
        for kind in ("xlsx", "zip"):
            self.assertEqual(self.client.get(self.url, {**self.query, "format": kind}).status_code, 403)
        self.assertEqual(self.client.get(reverse("staff:download_sheet", args=[self.sheet.pk])).status_code, 404)
        self.people["sam"].enabled = False
        self.people["sam"].save()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_own_empty_draft_is_downloadable_and_not_labelled_submitted(self):
        self.login("alex")
        response = self.client.get(reverse("staff:download_sheet", args=[self.sheet.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], XLSX_CONTENT_TYPE)
        cells, _, _ = workbook_cells(response.content)
        self.assertIn("Draft", cells["A1"]["value"])
        self.assertEqual(cells["G4"]["value"], "Not submitted")
        self.assertIn("not approved", cells["A7"]["value"])
        self.assertEqual(cells["F12"]["value"], "0")
        self.assertIsNone(cells["F12"]["formula"])
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_team_workbook_totals_keep_approved_and_pending_separate(self):
        self.add_shift()
        self.sheet = self.approve(self.sheet)
        draft = Timesheet.objects.create(staff=self.people["sam"], month=self.sheet.month)
        self.add_shift(draft)
        self.login()
        response = self.client.get(self.url, self.query)
        cells, _, _ = workbook_cells(response.content)
        self.assertEqual(cells["A7"]["value"], "Alex")
        self.assertEqual(cells["G7"]["value"], "7.5")
        self.assertEqual(cells["H7"]["value"], "0")
        self.assertEqual(cells["G12"]["value"], "7.5")
        self.assertEqual(cells["H12"]["value"], "7.5")
        with ZipFile(BytesIO(response.content)) as book:
            xml = book.read("xl/worksheets/sheet2.xml").decode()
            self.assertIn('<v>450</v>', xml)
            self.assertIn('BST', book.read("xl/sharedStrings.xml").decode())
        self.assertEqual(AuditEvent.objects.filter(action="Finance workbook downloaded").count(), 1)

    def test_zip_matches_month_employee_and_status_and_preserves_claim(self):
        self.add_shift()
        self.sheet = self.approve(self.sheet)
        Timesheet.objects.create(staff=self.people["alex"], month=dt.date(2026, 8, 1))
        Timesheet.objects.create(staff=self.people["sam"], month=self.sheet.month)
        self.login()
        response = self.client.get(self.url, {**self.query, "format": "zip", "employee": self.people["alex"].pk, "status": "approved"})
        self.assertEqual(response["Content-Type"], "application/zip")
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            filename = next(name for name in archive.namelist() if name.startswith("timesheet-"))
            self.assertIn(f"-ts{self.sheet.pk}-approved-", filename)
            cells, _, _ = workbook_cells(archive.read(filename))
            self.assertIn("Approved", cells["A1"]["value"])
            self.assertIn("Approved", cells["A7"]["value"])
            self.assertEqual(cells["F13"]["value"], "450")
        revision = self.sheet.revision
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.revision, revision)
        self.assertEqual(self.sheet.status, "approved")

    def test_duplicate_names_do_not_overwrite_individual_files(self):
        sam = self.people["sam"]
        sam.name = self.people["alex"].name
        sam.save()
        second = Timesheet.objects.create(staff=sam, month=self.sheet.month)
        self.login()
        response = self.client.get(self.url, {**self.query, "format": "zip"})
        with ZipFile(BytesIO(response.content)) as archive:
            names = [name for name in archive.namelist() if name.startswith("timesheet-")]
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(any(f"-ts{second.pk}-" in name for name in names))

    def test_empty_and_missing_results_have_summary_without_fabricated_timesheets(self):
        self.login()
        for filters, expected in (({"status": "missing"}, "Missing"), ({"status": "approved"}, None)):
            response = self.client.get(self.url, {**self.query, "format": "zip", **filters})
            with ZipFile(BytesIO(response.content)) as archive:
                self.assertEqual(len(archive.namelist()), 2)
                cells, _, _ = workbook_cells(archive.read("geezabreak-2026-09-team.xlsx"))
                if expected:
                    self.assertEqual(cells["D7"]["value"], expected)
                else:
                    self.assertNotIn("D7", cells)

    def test_staff_input_is_literal_in_excel_and_never_creates_external_links(self):
        malicious = '=HYPERLINK("https://example.test","click")'
        self.sheet.staff.name = malicious
        self.sheet.staff.save()
        self.add_shift(notes=malicious)
        self.login()
        response = self.client.get(self.url, self.query)
        cells, _, _ = workbook_cells(response.content)
        self.assertEqual(cells["A7"]["value"], malicious)
        self.assertIsNone(cells["A7"]["formula"])
        with ZipFile(BytesIO(response.content)) as book:
            self.assertFalse(any("externalLink" in name for name in book.namelist()))
            for name in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"):
                root = ET.fromstring(book.read(name))
                self.assertFalse(root.findall(".//{*}f"))
                self.assertFalse(root.findall(".//{*}hyperlink"))

    def test_invalid_filters_cannot_broaden_download(self):
        self.login()
        for query in ({"month": "bad"}, {"month": "2026-13"}, {"employee": "everyone"}, {"status": "unknown"}, {"format": "pdf"}):
            self.assertEqual(self.client.get(self.url, query).status_code, 404)

    def test_finance_lands_on_dashboard_and_employee_on_own_timesheets(self):
        self.login()
        self.assertRedirects(self.client.get(reverse("staff:login")), reverse("staff:reports"))
        self.login("alex")
        self.assertRedirects(self.client.get(reverse("staff:login")), reverse("staff:dashboard"))

    def test_dashboard_review_count_matches_selected_month(self):
        self.add_shift()
        transition(self.people["alex"].user, self.sheet.pk, self.sheet.revision, "submit", notify=False)
        self.login()
        response = self.client.get(reverse("staff:dashboard"), {"month": "2026-08"})
        self.assertEqual(response.context["pending_count"], 0)
        response = self.client.get(reverse("staff:dashboard"), self.query)
        self.assertEqual(response.context["pending_count"], 1)
        self.assertContains(response, "month=2026-09&amp;status=submitted")

    def test_confirmed_email_without_user_is_ready_to_invite(self):
        profile = StaffProfile(name="New colleague", email="new@example.test")
        self.assertEqual(profile.account_status, "Ready to invite")
        profile.email = None
        self.assertEqual(profile.account_status, "Awaiting email")
