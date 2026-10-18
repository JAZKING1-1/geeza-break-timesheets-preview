import datetime as dt
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .bulk import ENTRY_FIELDS, save_month
from .feedback import FeedbackForm, SECTIONS, confirm_non_respite, current_complete, save_feedback
from .models import AuditEvent, SessionFeedback, StaffProfile, TimeEntry, Timesheet, WorkCategory
from .services import EditConflict, delete_draft, save_entry, transition
from .test_excel_notifications import workbook_cells


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, TIMESHEET_EMAIL_DELIVERY="preview",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FeedbackTests(TestCase):
    def setUp(self):
        self.people = {}
        for name, role in (("Alex", "employee"), ("Other", "employee"), ("Finance", "finance"), ("Admin", "administrator")):
            user = get_user_model().objects.create_user(name, name.lower() + "@example.test", "fictional-password")
            self.people[name] = StaffProfile.objects.create(user=user, name=name, email=user.email, role=role)
        self.staff = self.people["Alex"]
        self.sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 10, 1))
        self.category = WorkCategory.objects.create(name="Respite Sitting")
        self.entry = TimeEntry(timesheet=self.sheet, date=dt.date(2026, 10, 5), end_date=dt.date(2026, 10, 5),
                               start_time=dt.time(9), end_time=dt.time(11), category=self.category)
        save_entry(self.staff.user, self.sheet.pk, 0, self.entry)
        self.sheet.refresh_from_db()
        self.url = reverse("staff:feedback", args=[self.sheet.pk, self.entry.pk])
        self.client.force_login(self.staff.user)

    def answers(self):
        data = {"applies": "yes", "confirmed": "on"}
        for heading, specs in SECTIONS:
            for key, label, kind in specs:
                data[key] = {"yesno": "no", "date": "2026-10-05", "time": "09:00",
                             "text": "Fictional example", "long": "Private fictional care answer"}[kind]
        data.update(session_completed="yes", planned_times="yes", departure_time="11:00",
                    outdoor="no", medication="no")
        return data

    def complete(self):
        record = save_feedback(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry.pk,
                               self.answers(), complete=True)
        self.sheet.refresh_from_db()
        return record

    def test_classic_submit_does_not_require_feedback(self):
        with patch("staff_portal.notifications.queue_submission") as send:
            result = transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit")
            self.assertEqual(result.status, "submitted")
            send.assert_called_once()
        self.assertFalse(SessionFeedback.objects.exists())

    def test_complete_feedback_and_weekly_claim_submission(self):
        self.complete()
        self.assertTrue(current_complete(self.entry))
        with patch("staff_portal.notifications.queue_submission") as send:
            result = transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit")
            send.assert_called_once()
        self.assertEqual(result.status, "submitted")
        second = Timesheet.objects.create(staff=self.staff, month=self.sheet.month)
        self.assertFalse(second.entries.exists())
        # Completing one claim does not pre-complete another week's feedback.
        entry = TimeEntry(timesheet=second, date=dt.date(2026, 10, 12), end_date=dt.date(2026, 10, 12),
                          start_time=dt.time(9), end_time=dt.time(11), category=self.category)
        save_entry(self.staff.user, second.pk, 0, entry)
        result = transition(self.staff.user, second.pk, 1, "submit", notify=False)
        self.assertEqual(result.status, "submitted")
        self.assertFalse(current_complete(entry))

    def test_respite_cannot_use_bulk_or_forged_not_applicable(self):
        confirm_non_respite(self.sheet)
        self.assertFalse(SessionFeedback.objects.exists())
        data = {"applies": "no", "not_applicable_reason": "Office work", "confirmed": "on"}
        response = self.client.post(self.url, {**data, "action": "complete", "revision": self.sheet.revision})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SessionFeedback.objects.exists())
        data = self.answers()
        data.update(applies="no", provider="Someone else", completed_by="Someone else", session_date="1999-01-01")
        record = save_feedback(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry.pk, data, complete=True)
        self.assertEqual(record.answers["applies"], "yes")
        self.assertEqual(record.answers["provider"], "Alex")
        self.assertEqual(record.answers["session_date"], "2026-10-05")

    def test_other_work_requires_explicit_confirmation(self):
        self.entry.category = WorkCategory.objects.create(name="Administration")
        save_entry(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry)
        self.sheet.refresh_from_db()
        incomplete = self.client.post(self.url, {"applies": "no", "action": "complete", "revision": self.sheet.revision})
        self.assertEqual(incomplete.status_code, 400)
        self.assertFalse(SessionFeedback.objects.exists())
        data = {"applies": "no", "not_applicable_reason": "Office administration", "confirmed": "on"}
        save_feedback(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry.pk, data, complete=True)
        self.sheet.refresh_from_db()
        transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit", notify=False)

    def test_required_conditional_answers_and_length_limits(self):
        for changes, field in [({"concerns": "yes", "reported": ""}, "reported"),
                               ({"planned_times": "no", "times_reason": ""}, "times_reason"),
                               ({"medication": "yes", "dosage": ""}, "dosage"),
                               ({"outdoor": "yes", "hazards": ""}, "hazards"),
                               ({"safe": "yes", "safe_detail": ""}, "safe_detail"),
                               ({"confirmed": ""}, "confirmed"),
                               ({"provider_feedback": "x" * 2001}, "provider_feedback")]:
            with self.subTest(field=field):
                form = FeedbackForm({**self.answers(), **changes}, entry=self.entry, complete=True)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_shift_change_invalidates_feedback_without_deleting_answers(self):
        record = self.complete()
        self.entry.end_time = dt.time(12)
        save_entry(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry)
        self.sheet.refresh_from_db()
        self.assertFalse(current_complete(self.entry))
        self.assertEqual(SessionFeedback.objects.get(pk=record.pk).answers["provider_feedback"], "Private fictional care answer")
        self.sheet = transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit", notify=False)
        self.complete()
        self.assertTrue(current_complete(self.entry))
        self.assertEqual(self.sheet.status, "submitted")

    def test_stale_feedback_cannot_overwrite_or_advance_revision(self):
        self.complete()
        before = SessionFeedback.objects.get().answers
        with self.assertRaises(EditConflict):
            save_feedback(self.staff.user, self.sheet.pk, 1, self.entry.pk, {"provider_feedback": "Stale"})
        self.assertEqual(SessionFeedback.objects.get().answers, before)
        response = self.client.post(self.url, {**self.answers(), "revision": 1, "action": "complete"})
        self.assertContains(response, 'name="revision" value="1"', status_code=409)

    def test_bulk_submit_saves_new_rows_with_incomplete_feedback(self):
        save_feedback(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry.pk, {"provider_feedback": "Saved for later"})
        self.sheet.refresh_from_db()
        row = {key: getattr(self.entry, key) for key in ENTRY_FIELDS}
        rows = [{**row, "entry_id": self.entry.pk}, {**row, "date": dt.date(2026, 10, 6), "end_date": dt.date(2026, 10, 6)}]
        with patch("staff_portal.notifications.queue_submission") as send:
            result = save_month(self.staff.user, self.sheet.pk, self.sheet.revision, rows, submit=True)
            send.assert_called_once()
        self.assertEqual(result.status, "submitted")
        self.assertEqual(self.sheet.entries.count(), 2)
        self.assertEqual(SessionFeedback.objects.count(), 1)
        self.assertIsNone(SessionFeedback.objects.get().completed_at)

    def test_owner_admin_finance_other_and_disabled_permissions(self):
        self.complete()
        for name, status in (("Alex", 200), ("Admin", 200), ("Other", 404), ("Finance", 404)):
            self.client.force_login(self.people[name].user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, status)
            if name != "Alex":
                post = self.client.post(self.url, {"revision": self.sheet.revision, "action": "draft"})
                self.assertEqual(post.status_code, 403 if name == "Admin" else 404)
        self.staff.enabled = False
        self.staff.save()
        self.client.force_login(self.staff.user)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_csrf_and_feedback_after_payroll_submission(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff.user)
        self.assertEqual(client.post(self.url, self.answers()).status_code, 403)
        self.sheet = transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit", notify=False)
        submitted_at = self.sheet.submitted_at
        with patch("staff_portal.notifications.queue_submission") as send:
            response = self.client.post(self.url, {**self.answers(), "action": "complete", "revision": self.sheet.revision})
            self.assertRedirects(response, reverse("staff:feedback_home"))
            send.assert_not_called()
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, "submitted")
        self.assertEqual(self.sheet.submitted_at, submitted_at)
        self.assertEqual(self.sheet.total_minutes, 120)
        self.assertTrue(current_complete(self.entry))

    def test_feedback_after_approval_preserves_payroll_and_review(self):
        self.sheet = transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit", notify=False)
        self.sheet = transition(self.people["Finance"].user, self.sheet.pk, self.sheet.revision, "approve")
        approved_at, reviewer = self.sheet.approved_at, self.sheet.reviewed_by_id
        self.complete()
        self.assertEqual(self.sheet.status, "approved")
        self.assertEqual(self.sheet.approved_at, approved_at)
        self.assertEqual(self.sheet.reviewed_by_id, reviewer)
        self.assertEqual(self.sheet.total_minutes, 120)
        self.assertTrue(current_complete(self.entry))

    def test_feedback_home_is_private_filterable_and_available_in_navigation(self):
        home = reverse("staff:feedback_home")
        self.assertContains(self.client.get(reverse("staff:dashboard")), 'href="' + home + '"')
        response = self.client.get(home)
        self.assertContains(response, self.url)
        self.assertContains(response, "Not started")
        self.assertContains(response, 'aria-label="Submit feedback" aria-current="page"')
        self.assertNotContains(self.client.get(home, {"month": "2026-09"}), self.url)
        self.assertContains(self.client.get(home, {"month": "2026-10"}), self.url)
        self.assertTrue(self.client.get(home, {"month": "bad"}).context["filter_form"].errors)
        self.complete()
        self.assertNotContains(self.client.get(home), self.url)
        self.assertContains(self.client.get(home, {"status": "submitted"}), self.url)
        self.assertContains(self.client.get(home, {"status": "all"}), self.url)
        for person in ("Other", "Finance", "Admin"):
            self.client.force_login(self.people[person].user)
            self.assertNotContains(self.client.get(home, {"status": "all", "staff": self.staff.pk}), self.url)
        self.client.logout()
        self.assertEqual(self.client.get(home).status_code, 302)

    def test_timesheet_has_no_feedback_step_and_feedback_draft_returns_home(self):
        response = self.client.get(reverse("staff:sheet", args=[self.sheet.pk]))
        self.assertNotContains(response, 'id="session-feedback"')
        self.assertNotContains(response, 'value="feedback"')
        self.assertNotContains(response, 'name="confirm_other_work"')
        response = self.client.post(self.url, {"action": "draft", "revision": self.sheet.revision})
        self.assertRedirects(response, reverse("staff:feedback_home"))
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, "draft")
        self.assertContains(self.client.get(reverse("staff:feedback_home")), "Draft")

    def test_feedback_home_pagination_preserves_filters(self):
        for day in range(6, 27):
            entry = TimeEntry(timesheet=self.sheet, date=dt.date(2026, 10, day), end_date=dt.date(2026, 10, day),
                              start_time=dt.time(9), end_time=dt.time(11), category=self.category)
            entry.clean_timing()
            entry.save()
        response = self.client.get(reverse("staff:feedback_home"), {"status": "all", "month": "2026-10"})
        self.assertEqual(len(response.context["page"]), 20)
        self.assertContains(response, "month=2026-10&amp;status=all&amp;page=2")
        response = self.client.get(reverse("staff:feedback_home"), {"status": "all", "month": "2026-10", "page": 2})
        self.assertEqual(len(response.context["page"]), 2)

    def test_care_answers_do_not_leak_to_finance_excel_email_or_audit(self):
        self.complete()
        private = "Private fictional care answer"
        self.client.force_login(self.people["Finance"].user)
        response = self.client.get(reverse("staff:sheet", args=[self.sheet.pk]))
        self.assertContains(response, "Complete")
        self.assertNotContains(response, private)
        self.assertNotContains(response, self.url)
        excel = self.client.get(reverse("staff:download_sheet", args=[self.sheet.pk]))
        cells, _, _ = workbook_cells(excel.content)
        self.assertNotIn(private, json.dumps(cells))
        self.assertNotIn(private, json.dumps(list(AuditEvent.objects.values_list("detail", flat=True))))
        # The existing notification snapshot contains only timesheet data.
        from .notifications import queue_submission
        self.sheet = transition(self.staff.user, self.sheet.pk, self.sheet.revision, "submit", notify=False)
        with self.captureOnCommitCallbacks(execute=False):
            notice = queue_submission(self.sheet)
        notice = self.sheet.notifications.get()
        self.assertNotIn(private, notice.body)
        cells, _, _ = workbook_cells(bytes(notice.attachment_content))
        self.assertNotIn(private, json.dumps(cells))

    def test_draft_feedback_save_preserves_escaped_text_and_unknown_keys_ignored(self):
        response = self.client.post(self.url, {"action": "draft", "revision": self.sheet.revision,
                                              "provider_feedback": "<script>alert('x')</script>", "role": "admin"})
        self.assertEqual(response.status_code, 302)
        record = SessionFeedback.objects.get()
        self.assertIsNone(record.completed_at)
        self.assertNotIn("role", record.answers)
        response = self.client.get(self.url)
        self.assertNotContains(response, "<script>alert('x')</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_draft_delete_removes_its_feedback_but_keeps_audit(self):
        self.complete()
        delete_draft(self.staff.user, self.sheet.pk, self.sheet.revision)
        self.assertFalse(SessionFeedback.objects.exists())
        self.assertTrue(AuditEvent.objects.filter(action="Session feedback completed").exists())

    def test_form_includes_project_fields_and_excludes_removed_safety_questions(self):
        response = self.client.get(self.url)
        for label in ("Were there any concerns during this session", "Did you report your concerns to line manager?",
                      "Respite provider feedback", "GIRFEC and activities", "Activities undertaken", "Dosage"):
            self.assertContains(response, label)
        self.assertNotContains(response, "minimum level of safety")
        self.assertContains(response, "not sent to Charitylog")

    def test_inapplicable_sections_do_not_store_hidden_medication_details(self):
        data = self.answers()
        data.update(medication="no", dosage="Irrelevant private detail", administered_date="invalid")
        record = save_feedback(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry.pk, data, complete=True)
        self.assertEqual(record.answers["dosage"], "")
        self.assertIsNone(record.answers["administered_date"])

    def test_bulk_declaration_does_not_replace_full_feedback_for_other_categories(self):
        self.entry.category = WorkCategory.objects.create(name="Family Support")
        save_entry(self.staff.user, self.sheet.pk, self.sheet.revision, self.entry)
        self.sheet.refresh_from_db()
        record = self.complete()
        before = record.answers
        confirm_non_respite(self.sheet)
        record.refresh_from_db()
        self.assertEqual(record.answers, before)
        self.assertEqual(record.answers["applies"], "yes")
