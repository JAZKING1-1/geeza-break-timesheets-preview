import datetime as dt
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .bulk import monthly_formset
from .models import StaffProfile, SubmissionNotification, TimeEntry, Timesheet, WorkCategory
from .notifications import deliver_notification
from .services import save_entry, transition


@override_settings(PORTAL_PREVIEW=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
                   TIMESHEET_EMAIL_DELIVERY="preview", TIMESHEET_NOTIFICATION_EMAIL="dev@example.test")
class MonthlyEditorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", "staff@example.test", "A long test password")
        self.staff = StaffProfile.objects.create(user=self.user, name="Test Employee", job_title="Support worker")
        self.sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 9, 1))
        self.category = WorkCategory.objects.create(name="Family Support")
        self.client.force_login(self.user)
        self.url = reverse("staff:sheet", args=[self.sheet.pk])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = override_settings(EMAIL_FILE_PATH=Path(self.temp.name))
        self.config.enable()
        self.addCleanup(self.config.disable)

    def row(self, **values):
        data = {"entry_id": "", "date": "2026-09-01", "start_time": "09:00", "end_time": "17:00",
                "break_minutes": "30", "category": str(self.category.pk), "notes": "Family support visit",
                "end_date": "", "start_fold": "0", "end_fold": "0"}
        data.update(values)
        return data

    def post(self, rows, action="save", revision=None):
        self.sheet.refresh_from_db()
        data = {"revision": self.sheet.revision if revision is None else revision, "action": action,
                "rows-TOTAL_FORMS": len(rows), "rows-INITIAL_FORMS": self.sheet.entries.count(),
                "rows-MAX_NUM_FORMS": 200}
        for index, row in enumerate(rows):
            data.update({f"rows-{index}-{key}": value for key, value in row.items()})
        return self.client.post(self.url, data)

    def test_many_rows_saved_and_submitted_together_with_one_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post([self.row(), self.row(date="2026-09-02", notes="Training session")], "submit")
        self.assertRedirects(response, self.url)
        self.sheet.refresh_from_db()
        self.assertEqual((self.sheet.status, self.sheet.revision, self.sheet.total_minutes), ("submitted", 1, 900))
        notice = SubmissionNotification.objects.get()
        self.assertEqual((notice.recipient, notice.status), ("dev@example.test", "preview"))
        self.assertNotIn("Training session", notice.body)
        self.assertIn(self.url, notice.body)
        self.assertEqual(len(list(Path(self.temp.name).glob("*.log"))), 1)
        self.assertEqual(self.post([], "submit").status_code, 403)
        deliver_notification(notice.pk)
        self.assertEqual(SubmissionNotification.objects.count(), 1)
        self.assertEqual(len(list(Path(self.temp.name).glob("*.log"))), 1)

    def test_draft_saves_rows_without_email(self):
        self.assertEqual(self.post([self.row(), self.row(date="2026-09-02")]).status_code, 302)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, "draft")
        self.assertEqual(self.sheet.entries.count(), 2)
        self.assertFalse(SubmissionNotification.objects.exists())

    def test_blank_extra_row_is_ignored(self):
        blank = {"break_minutes": "0", "start_fold": "0", "end_fold": "0"}
        self.assertEqual(self.post([self.row(), blank]).status_code, 302)
        self.assertEqual(self.sheet.entries.count(), 1)

    def test_invalid_row_preserves_all_input_and_no_partial_save(self):
        response = self.post([self.row(notes="Keep this purpose"), self.row(date="2026-09-02", start_time="")])
        self.assertContains(response, "Keep this purpose", status_code=400)
        self.assertContains(response, "This field is required", status_code=400)
        self.assertEqual(self.sheet.entries.count(), 0)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.revision, 0)

    def test_overlap_between_unsaved_rows_is_reported_and_rolled_back(self):
        response = self.post([self.row(), self.row(start_time="16:00", end_time="19:00")], "submit")
        self.assertContains(response, "overlaps row 2", status_code=400)
        self.assertEqual(self.sheet.entries.count(), 0)
        self.assertFalse(SubmissionNotification.objects.exists())

    def test_swapping_saved_dates_and_deleting_a_row_is_atomic(self):
        self.post([self.row(), self.row(date="2026-09-02"), self.row(date="2026-09-03")])
        first, second, third = list(self.sheet.entries.all())
        response = self.post([self.row(entry_id=first.pk, date="2026-09-02"),
                              self.row(entry_id=second.pk, date="2026-09-01"),
                              {"entry_id": third.pk, "DELETE": "on"}])
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.date, dt.date(2026, 9, 2))
        self.assertEqual(self.sheet.entries.count(), 2)
        self.assertTrue(self.sheet.events.filter(action="Entry removed").exists())

    def test_omitted_duplicate_and_foreign_ids_cannot_overwrite(self):
        self.post([self.row()])
        entry = self.sheet.entries.get()
        for rows in [[], [self.row(entry_id=entry.pk), self.row(entry_id=entry.pk)], [self.row(entry_id=999999)]]:
            with self.subTest(rows=rows):
                self.assertEqual(self.post(rows).status_code, 409)
        self.assertEqual(self.sheet.entries.count(), 1)

    def test_other_employee_cannot_post_rows(self):
        user = get_user_model().objects.create_user("other", password="Long password for testing")
        StaffProfile.objects.create(user=user, name="Other employee", job_title="Support worker")
        self.client.force_login(user)
        self.assertEqual(self.post([self.row()]).status_code, 404)

    def test_stale_editor_keeps_old_revision_and_cannot_overwrite(self):
        self.post([self.row()])
        entry = self.sheet.entries.get()
        response = self.post([self.row(entry_id=entry.pk, notes="stale")], revision=0)
        self.assertContains(response, 'name="revision" value="0"', status_code=409)
        entry.refresh_from_db()
        self.assertNotEqual(entry.notes, "stale")

    def test_no_javascript_add_rows_keeps_entered_values(self):
        response = self.post([self.row(notes="Unsaved purpose")], action="add_rows")
        self.assertContains(response, "Unsaved purpose")
        self.assertEqual(response.context["editor"].total_form_count(), 4)
        self.assertTrue(response.context["editor"].is_valid())
        self.assertFalse(self.sheet.entries.exists())
        payload = response.context["editor"].data.copy()
        payload["action"] = "save"
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertEqual(self.sheet.entries.count(), 1)

    def test_empty_submission_rejected_and_delete_can_be_undone_before_save(self):
        self.assertEqual(self.post([], "submit").status_code, 409)
        self.post([self.row()])
        item = self.sheet.entries.get()
        response = self.post([self.row(entry_id=item.pk, DELETE="on")], "submit")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(TimeEntry.objects.filter(pk=item.pk).exists())

    def test_bad_month_and_overnight_boundaries(self):
        self.assertEqual(self.post([self.row(date="2026-10-01")]).status_code, 400)
        self.assertEqual(self.post([self.row(date="2026-09-30", start_time="22:00", end_time="02:00", end_date="2026-10-01")]).status_code, 400)
        self.assertEqual(self.post([self.row(date="2026-09-30", start_time="22:00", end_time="00:00", end_date="2026-10-01")]).status_code, 302)
        self.assertEqual(self.sheet.total_minutes, 90)

    def test_management_form_and_row_limit_cannot_be_bypassed(self):
        self.assertEqual(self.client.post(self.url, {"action": "submit", "revision": 0}).status_code, 400)
        self.assertEqual(self.post([{}] * 201).status_code, 400)
        self.assertFalse(self.sheet.entries.exists())

    def test_full_size_month_fits_request_field_limit(self):
        response = self.post([self.row() for _ in range(200)], "add_rows")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["editor"].total_form_count(), 200)

    def test_legacy_add_link_opens_month_editor(self):
        self.assertRedirects(self.client.get(reverse("staff:entry_add", args=[self.sheet.pk])), self.url)

    @override_settings(TIMESHEET_EMAIL_DELIVERY="mailjet", TIMESHEET_MAILJET_API_KEY="test-key",
                       TIMESHEET_MAILJET_API_SECRET="test-secret")
    @patch("staff_portal.notifications.Client")
    def test_mailjet_receives_only_intended_recipient_once(self, send):
        send.return_value.send.create.return_value = Mock(json=lambda: {"Messages": [{"Status": "success"}]})
        with self.captureOnCommitCallbacks(execute=True):
            self.post([self.row()], "submit")
        notice = SubmissionNotification.objects.get()
        self.assertEqual(notice.status, "sent")
        payload = send.return_value.send.create.call_args.kwargs["data"]["Messages"][0]
        self.assertEqual(payload["To"], [{"Email": "dev@example.test"}])
        self.assertNotIn("Cc", payload)
        self.assertNotIn("Bcc", payload)
        deliver_notification(notice.pk)
        send.assert_called_once()

    @override_settings(TIMESHEET_EMAIL_DELIVERY="mailjet", TIMESHEET_MAILJET_API_KEY="test-key",
                       TIMESHEET_MAILJET_API_SECRET="test-secret")
    @patch("staff_portal.notifications.Client", side_effect=TimeoutError)
    def test_email_failure_does_not_lose_timesheet_or_resend_automatically(self, send):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post([self.row()], "submit").status_code, 302)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, "submitted")
        notice = SubmissionNotification.objects.get()
        self.assertEqual(notice.status, "failed")
        deliver_notification(notice.pk)
        send.assert_called_once()

    def test_classic_submit_route_also_queues_notification(self):
        self.post([self.row()])
        self.sheet.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            transition(self.user, self.sheet.pk, self.sheet.revision, "submit")
        self.assertEqual(SubmissionNotification.objects.get().status, "preview")

    def test_failed_or_returned_edit_does_not_queue_email_until_resubmission(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post([self.row()], "submit")
        reviewer = get_user_model().objects.create_user("finance", password="Long test password")
        StaffProfile.objects.create(user=reviewer, name="Finance", job_title="Finance", role="finance")
        self.sheet.refresh_from_db()
        transition(reviewer, self.sheet.pk, self.sheet.revision, "return", "Check purpose")
        entry = self.sheet.entries.get()
        self.post([self.row(entry_id=entry.pk, notes="Corrected purpose")])
        self.assertEqual(SubmissionNotification.objects.count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.post([self.row(entry_id=entry.pk, notes="Corrected purpose")], "submit")
        self.assertEqual(SubmissionNotification.objects.count(), 2)
