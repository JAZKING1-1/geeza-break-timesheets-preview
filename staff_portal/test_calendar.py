import datetime as dt
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .bulk import monthly_formset
from .models import StaffProfile, SubmissionNotification, TimeEntry, Timesheet, WorkCategory
from .service_catalog import SERVICES, sync_service_catalog
from .services import save_entry


@override_settings(PORTAL_PREVIEW=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WorkDatePickerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("calendar-staff")
        self.staff = StaffProfile.objects.create(user=self.user, name="Calendar employee", job_title="Support")
        self.client.force_login(self.user)

    def test_date_opens_correct_month_and_prefills_only_new_row(self):
        response = self.client.post(reverse("staff:new_sheet"), {"work_date": "2028-02-29"})
        sheet = Timesheet.objects.get(staff=self.staff, month=dt.date(2028, 2, 1))
        url = reverse("staff:sheet", args=[sheet.pk])
        self.assertRedirects(response, url + "?date=2028-02-29#entry-grid")
        response = self.client.get(url, {"date": "2028-02-29"})
        field = response.context["editor"].forms[0]["date"]
        self.assertEqual(field.value(), dt.date(2028, 2, 29))
        self.assertEqual(field.field.widget.input_type, "date")
        self.assertEqual(field.field.widget.attrs["max"], "2028-02-29")
        self.assertFalse(sheet.entries.exists())
        self.assertContains(response, 'data-calendar-for="id_work_date"')
        self.assertContains(response, 'name="work_date"')

    def test_start_page_does_not_create_records_and_invalid_date_stays_on_form(self):
        url = reverse("staff:new_sheet")
        self.assertContains(self.client.get(url), "Continue to shifts")
        self.assertFalse(Timesheet.objects.exists())
        response = self.client.post(url, {"work_date": "2028-02-30"})
        self.assertContains(response, "Enter a valid date", status_code=400)
        self.assertFalse(Timesheet.objects.exists())

    def test_submitted_month_is_explained_without_reopening_or_duplicating_it(self):
        sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2028, 2, 1), status="submitted")
        response = self.client.post(reverse("staff:new_sheet"), {"work_date": "2028-02-29"})
        self.assertContains(response, "has already been submitted", status_code=409)
        self.assertContains(response, reverse("staff:sheet", args=[sheet.pk]), status_code=409)
        self.assertEqual(Timesheet.objects.count(), 1)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, "submitted")

    def test_dates_in_all_months_and_years_reuse_existing_timesheets(self):
        for month in range(1, 13):
            value = f"2028-{month:02}-15"
            for _ in range(2):
                self.assertEqual(self.client.post(reverse("staff:new_sheet"), {"work_date": value}).status_code, 302)
        self.client.post(reverse("staff:new_sheet"), {"work_date": "2027-12-31"})
        self.assertEqual(Timesheet.objects.filter(staff=self.staff).count(), 13)
        response = self.client.get(reverse("staff:dashboard"))
        self.assertContains(response, "December 2027")
        self.assertContains(response, "December 2028")
        self.assertNotContains(response, 'id="year-calendar"')

    def test_invalid_dates_create_nothing_and_month_scope_cannot_be_bypassed(self):
        for value in ["", "not-a-date", "2027-02-29", "2028-02-30"]:
            self.client.post(reverse("staff:new_sheet"), {"work_date": value})
        self.assertFalse(Timesheet.objects.exists())
        sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2028, 2, 1))
        form = monthly_formset(sheet).forms[0]
        form.cleaned_data = {"date": dt.date(2028, 3, 1)}
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            form.clean_date()
        response = self.client.get(reverse("staff:sheet", args=[sheet.pk]), {"date": "2028-03-01"})
        self.assertIsNone(response.context["editor"].forms[0]["date"].value())

    def test_date_picker_preserves_other_employees_records(self):
        colleague = StaffProfile.objects.create(name="Another employee", job_title="Support")
        other = Timesheet.objects.create(staff=colleague, month=dt.date(2028, 2, 1))
        self.client.post(reverse("staff:new_sheet"), {"work_date": "2028-02-29", "staff": colleague.pk})
        self.assertEqual(Timesheet.objects.filter(staff=self.staff).count(), 1)
        self.assertEqual(self.client.get(reverse("staff:sheet", args=[other.pk])).status_code, 404)

    def test_service_catalog_preserves_legacy_entries_but_prevents_new_legacy_claims(self):
        legacy = WorkCategory.objects.create(name="Respite")
        sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 9, 1))
        entry = TimeEntry(timesheet=sheet, date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 1),
                          start_time=dt.time(9), end_time=dt.time(17), category=legacy, break_minutes=30)
        save_entry(self.user, sheet.pk, 0, entry)
        sync_service_catalog()
        sync_service_catalog()
        self.assertEqual(WorkCategory.objects.filter(name__in=SERVICES, active=True).count(), 6)
        legacy.refresh_from_db()
        entry.refresh_from_db()
        self.assertFalse(legacy.active)
        self.assertEqual(entry.category_id, legacy.pk)
        self.assertIn(legacy, monthly_formset(sheet).forms[0].fields["category"].queryset)
        new_sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 10, 1))
        self.assertNotIn(legacy, monthly_formset(new_sheet).forms[0].fields["category"].queryset)


@override_settings(PORTAL_PREVIEW=True, PREVIEW_ACCESS_CODE="test-access-code-for-seed-calendar",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
                   TIMESHEET_EMAIL_DELIVERY="mailjet", TIMESHEET_MAILJET_API_KEY="test-key",
                   TIMESHEET_MAILJET_API_SECRET="test-secret", TIMESHEET_FROM_EMAIL="sender@example.org",
                   TIMESHEET_NOTIFICATION_EMAIL="reviewer@example.org")
class SeedNotificationTests(TestCase):
    @patch("staff_portal.notifications.Client")
    def test_fresh_start_and_repeat_bootstrap_never_queue_or_send_claim_emails(self, send):
        with self.captureOnCommitCallbacks(execute=True):
            call_command("bootstrap_preview", stdout=io.StringIO())
            call_command("bootstrap_preview", stdout=io.StringIO())
        self.assertTrue(Timesheet.objects.filter(status="submitted").exists())
        self.assertFalse(SubmissionNotification.objects.exists())
        send.assert_not_called()
