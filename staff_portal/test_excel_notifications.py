import base64
import datetime as dt
import json
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import requests

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings

from .excel import XLSX_CONTENT_TYPE, submission_workbook
from .models import StaffProfile, SubmissionNotification, TimeEntry, Timesheet, WorkCategory
from .notifications import deliver_notification, queue_submission
from .services import save_entry, transition


def workbook_cells(content):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(content)) as archive:
        strings = ["".join(item.itertext()) for item in
                   ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        cells = {}
        for cell in sheet.findall(".//s:c", ns):
            value = cell.findtext("s:v", namespaces=ns)
            cells[cell.attrib["r"]] = {
                "value": strings[int(value)] if cell.attrib.get("t") == "s" else value,
                "formula": cell.findtext("s:f", namespaces=ns),
            }
        return cells, archive.namelist(), sheet


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, TIMESHEET_EMAIL_DELIVERY="mailjet",
                   TIMESHEET_MAILJET_API_KEY="test-key", TIMESHEET_MAILJET_API_SECRET="test-secret",
                   TIMESHEET_FROM_EMAIL="sender@example.org", TIMESHEET_NOTIFICATION_EMAIL="dev@example.org",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExcelSubmissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("employee", "employee@example.org")
        self.staff = StaffProfile.objects.create(user=self.user, name="Test Employee", job_title="Support",
                                                  email=self.user.email)
        self.category = WorkCategory.objects.create(name="Family Support")
        self.sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 9, 1))
        self.entry = TimeEntry(timesheet=self.sheet, date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 1),
                               start_time=dt.time(9), end_time=dt.time(17), break_minutes=30,
                               category=self.category, notes="Family support visit")
        save_entry(self.user, self.sheet.pk, 0, self.entry)
        self.sheet.refresh_from_db()

    def submit(self, execute=True):
        with self.captureOnCommitCallbacks(execute=execute):
            self.sheet = transition(self.user, self.sheet.pk, self.sheet.revision, "submit")
        return SubmissionNotification.objects.get(timesheet=self.sheet, revision=self.sheet.revision)

    @patch("staff_portal.notifications.Client")
    def test_real_excel_attachment_and_exact_recipient(self, client):
        client.return_value.send.create.return_value = Mock(json=lambda: {"Messages": [{"Status": "success"}]})
        notice = self.submit()
        self.assertEqual(notice.status, "sent")
        client.assert_called_once_with(auth=("test-key", "test-secret"), version="v3.1")
        send = client.return_value.send.create
        self.assertEqual(send.call_args.kwargs["timeout"], 20)
        message = send.call_args.kwargs["data"]["Messages"][0]
        self.assertEqual(message["To"], [{"Email": "dev@example.org"}])
        self.assertNotIn("Cc", message)
        self.assertNotIn("Bcc", message)
        attachment, = message["Attachments"]
        self.assertEqual(attachment["ContentType"], XLSX_CONTENT_TYPE)
        self.assertTrue(attachment["Filename"].endswith(f"-2026-09-ts{self.sheet.pk}-r2.xlsx"))
        content = base64.b64decode(attachment["Base64Content"], validate=True)
        self.assertEqual(content, bytes(notice.attachment_content))
        cells, _, _ = workbook_cells(content)
        self.assertEqual(cells["B3"]["value"], "Test Employee")
        self.assertEqual(cells["B5"]["value"], "employee@example.org")
        self.assertEqual(cells["B11"]["value"], "Family Support")
        self.assertEqual(cells["I11"]["value"], "Family support visit")
        self.assertEqual(cells["F11"]["value"], "450")
        self.assertEqual(cells["H13"]["value"], "7.5")
        self.assertEqual(cells["F13"]["formula"], "SUM(F11:F11)")
        deliver_notification(notice.pk)
        send.assert_called_once()

    @override_settings(TIMESHEET_EMAIL_DELIVERY="preview")
    @patch("staff_portal.notifications.Client")
    def test_local_preview_has_same_excel_and_never_calls_mailjet(self, client):
        with tempfile.TemporaryDirectory() as folder, override_settings(EMAIL_FILE_PATH=folder):
            notice = self.submit()
            email_file, = Path(folder).glob("*.log")
            message = BytesParser(policy=policy.default).parsebytes(email_file.read_bytes())
            attachment, = message.iter_attachments()
            self.assertEqual(attachment.get_filename(), notice.attachment_name)
            self.assertEqual(attachment.get_content_type(), XLSX_CONTENT_TYPE)
            self.assertEqual(attachment.get_payload(decode=True), bytes(notice.attachment_content))
            self.assertEqual(notice.status, "preview")
        client.assert_not_called()

    @patch("mailjet_rest.client.requests.post")
    def test_actual_mailjet_client_serializes_attachment_with_bounded_timeout(self, post):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"Messages":[{"Status":"success"}]}'
        post.return_value = response
        notice = self.submit()
        self.assertEqual(notice.status, "sent")
        self.assertEqual(post.call_args.args[0], "https://api.mailjet.com/v3.1/send")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["auth"], ("test-key", "test-secret"))
        self.assertEqual(kwargs["timeout"], 20)
        self.assertTrue(kwargs["verify"])
        attachment = json.loads(kwargs["data"])["Messages"][0]["Attachments"][0]
        self.assertEqual(base64.b64decode(attachment["Base64Content"]), bytes(notice.attachment_content))

    @patch("staff_portal.notifications.Client")
    def test_delayed_send_uses_original_submission_even_after_corrections(self, client):
        client.return_value.send.create.return_value = Mock(json=lambda: {"Messages": [{"Status": "success"}]})
        notice = self.submit(execute=False)
        original = bytes(notice.attachment_content)
        reviewer = get_user_model().objects.create_user("reviewer")
        StaffProfile.objects.create(user=reviewer, name="Reviewer", role="finance")
        self.sheet = transition(reviewer, self.sheet.pk, self.sheet.revision, "return", "Correct the purpose")
        self.entry.notes = "Corrected purpose"
        save_entry(self.user, self.sheet.pk, self.sheet.revision, self.entry)
        self.sheet.refresh_from_db()
        self.category.name = "Renamed service"
        self.category.save()
        self.staff.name = "Renamed employee"
        self.staff.save()
        deliver_notification(notice.pk)
        payload = client.return_value.send.create.call_args.kwargs["data"]["Messages"][0]
        self.assertEqual(base64.b64decode(payload["Attachments"][0]["Base64Content"]), original)
        latest = self.submit(execute=False)
        cells, _, _ = workbook_cells(bytes(latest.attachment_content))
        self.assertEqual(cells["I11"]["value"], "Corrected purpose")
        self.assertNotEqual(notice.attachment_name, latest.attachment_name)
        self.assertEqual(SubmissionNotification.objects.count(), 2)
        self.assertEqual(queue_submission(self.sheet).pk, latest.pk)

    def test_staff_text_cannot_create_formulas_links_or_external_references(self):
        text = '=HYPERLINK("https://example.org","do not run")'
        self.staff.name = text
        self.staff.save()
        self.category.name = "https://example.org"
        self.category.save()
        self.entry.notes = text
        self.entry.save()
        notice = self.submit(execute=False)
        cells, filenames, _ = workbook_cells(bytes(notice.attachment_content))
        for address in ["B3", "I11"]:
            self.assertEqual(cells[address]["value"], text)
            self.assertIsNone(cells[address]["formula"])
        self.assertFalse(any("externalLink" in name for name in filenames))
        self.assertNotIn("xl/worksheets/_rels/sheet1.xml.rels", filenames)
        self.assertNotIn("/", notice.attachment_name)

    @patch("staff_portal.notifications.Client")
    def test_provider_rejection_keeps_claim_and_does_not_report_sent(self, client):
        client.return_value.send.create.return_value = Mock(json=lambda: {"Messages": [{"Status": "error"}]})
        notice = self.submit()
        self.assertEqual(notice.status, "failed")
        self.assertIsNone(notice.delivered_at)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, "submitted")
        self.assertTrue(notice.attachment_content)

    @patch("staff_portal.notifications.Client")
    def test_rollback_does_not_leave_snapshot_or_send_email(self, client):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError), transaction.atomic():
                transition(self.user, self.sheet.pk, self.sheet.revision, "submit")
                raise RuntimeError("Transaction cancelled")
        self.assertFalse(SubmissionNotification.objects.exists())
        client.assert_not_called()

    @patch("staff_portal.notifications.Client")
    def test_legacy_pending_notice_without_snapshot_is_not_sent(self, client):
        notice = SubmissionNotification.objects.create(timesheet=self.sheet, revision=1,
                                                          recipient="dev@example.org", subject="Old", body="Old")
        deliver_notification(notice.pk)
        notice.refresh_from_db()
        self.assertEqual(notice.status, "failed")
        client.assert_not_called()

    def test_worked_hours_use_elapsed_time_across_clock_change(self):
        sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 10, 1))
        entry = TimeEntry(timesheet=sheet, date=dt.date(2026, 10, 25), end_date=dt.date(2026, 10, 25),
                          start_time=dt.time(0), end_time=dt.time(4), break_minutes=30, category=self.category)
        save_entry(self.user, sheet.pk, 0, entry)
        sheet.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=False):
            sheet = transition(self.user, sheet.pk, sheet.revision, "submit")
        _, content = submission_workbook(sheet)
        cells, _, _ = workbook_cells(content)
        self.assertEqual(cells["F11"]["value"], "270")
        self.assertIn("BST", cells["C11"]["value"])
        self.assertIn("GMT", cells["D11"]["value"])
        self.assertEqual(cells["H13"]["value"], "4.5")
