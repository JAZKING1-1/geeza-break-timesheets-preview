import datetime as dt
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .bulk import monthly_formset
from .calendar_view import annual_calendar
from .models import StaffProfile, SubmissionNotification, TimeEntry, Timesheet, WorkCategory
from .service_catalog import SERVICES, sync_service_catalog
from .services import save_entry


@override_settings(PORTAL_PREVIEW=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AnnualCalendarTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("calendar-staff")
        self.staff = StaffProfile.objects.create(user=self.user, name="Calendar employee", job_title="Support")
        self.client.force_login(self.user)

    def test_twelve_months_and_leap_year_days(self):
        for year, february_days in [(2026, 28), (2028, 29), (2100, 28), (2000, 29)]:
            months = annual_calendar(year, [], dt.date(2026, 9, 21))
            self.assertEqual([m["month"].month for m in months], list(range(1, 13)))
            days = [d["number"] for week in months[1]["weeks"] for d in week if d["number"]]
            self.assertEqual(days, list(range(1, february_days + 1)))

    def test_selected_year_is_private_and_get_does_not_create_sheets(self):
        own = Timesheet.objects.create(staff=self.staff, month=dt.date(2028, 12, 1))
        Timesheet.objects.create(staff=self.staff, month=dt.date(2027, 12, 1))
        colleague = StaffProfile.objects.create(name="Another employee", job_title="Support")
        Timesheet.objects.create(staff=colleague, month=dt.date(2028, 1, 1), status="approved")
        response = self.client.get(reverse("staff:dashboard"), {"year": "2028"})
        self.assertEqual(response.context["calendar_year"], 2028)
        self.assertEqual(response.context["sheets"], [own])
        self.assertIsNone(response.context["calendar_months"][0]["sheet"])
        self.assertEqual(response.context["calendar_months"][11]["sheet"], own)
        self.assertContains(response, 'aria-label="Start February 2028"')
        self.assertContains(response, 'aria-label="Open December 2028"')
        self.assertEqual(Timesheet.objects.count(), 3)

    def test_invalid_year_has_feedback_and_extremes_do_not_crash(self):
        for year in ["oops", "0", "10000", "9" * 60]:
            response = self.client.get(reverse("staff:dashboard"), {"year": year})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["year_form"].errors)
        for year in [1, 9999]:
            response = self.client.get(reverse("staff:dashboard"), {"year": year})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["calendar_months"]), 12)

    def test_each_month_opens_its_own_sheet_without_duplicates(self):
        for month in range(1, 13):
            value = f"2028-{month:02}"
            response = self.client.post(reverse("staff:new_sheet"), {"month": value})
            sheet = Timesheet.objects.get(staff=self.staff, month=f"{value}-01")
            self.assertRedirects(response, reverse("staff:sheet", args=[sheet.pk]))
            self.client.post(reverse("staff:new_sheet"), {"month": value})
            dates = dict(monthly_formset(sheet).forms[0].fields["date"].choices)
            if month == 2:
                self.assertIn("2028-02-29", dates)
            self.assertNotIn("2028-13-01", dates)
        self.assertEqual(Timesheet.objects.filter(staff=self.staff).count(), 12)

    def test_saved_day_markers_and_totals(self):
        sheet = Timesheet.objects.create(staff=self.staff, month=dt.date(2026, 9, 1))
        category = WorkCategory.objects.create(name="Family Support")
        save_entry(self.user, sheet.pk, 0, TimeEntry(timesheet=sheet, date=dt.date(2026, 9, 21),
            end_date=dt.date(2026, 9, 21), start_time=dt.time(9), end_time=dt.time(17),
            break_minutes=30, category=category))
        months = annual_calendar(2026, [sheet], dt.date(2026, 9, 21))
        day = next(d for w in months[8]["weeks"] for d in w if d["number"] == 21)
        self.assertTrue(day["today"])
        self.assertEqual(day["shifts"], 1)
        self.assertEqual(months[8]["sheet"].total_display, "7h 30m")

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
    @patch("staff_portal.notifications.requests.post")
    def test_fresh_start_and_repeat_bootstrap_never_queue_or_send_claim_emails(self, send):
        with self.captureOnCommitCallbacks(execute=True):
            call_command("bootstrap_preview", stdout=io.StringIO())
            call_command("bootstrap_preview", stdout=io.StringIO())
        self.assertTrue(Timesheet.objects.filter(status="submitted").exists())
        self.assertFalse(SubmissionNotification.objects.exists())
        send.assert_not_called()
