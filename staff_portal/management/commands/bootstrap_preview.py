import datetime as dt

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from staff_portal.models import StaffProfile, TimeEntry, Timesheet, WorkCategory
from staff_portal.services import save_entry, transition
from staff_portal.service_catalog import sync_service_catalog
from staff_portal.feedback import confirm_non_respite


class Command(BaseCommand):
    help = "Seed fictional review accounts only. Never imports payroll staff or private databases."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.PORTAL_PREVIEW or len(settings.PREVIEW_ACCESS_CODE) < 24:
            raise CommandError("The review seed requires an explicit preview configuration and private access code.")
        if StaffProfile.objects.filter(is_demo=False).exists():
            raise CommandError("Refusing to seed a database containing real staff profiles.")
        sync_service_catalog()
        profiles = {}
        for role, name, title in [
            ("employee", "Alex Morgan (demo)", "Family Support Worker"),
            ("finance", "Taylor Reed (demo)", "Finance reviewer"),
            ("administrator", "Jamie Ellis (demo)", "Staff administrator"),
        ]:
            address = f"{role}@example.test"
            user, _ = get_user_model().objects.get_or_create(username="demo-" + role,
                defaults={"email": address, "is_active": True})
            if not user.check_password(settings.PREVIEW_ACCESS_CODE):
                user.set_password(settings.PREVIEW_ACCESS_CODE)
                user.save(update_fields=["password"])
            profile, _ = StaffProfile.objects.get_or_create(user=user, defaults={
                "name": name, "job_title": title, "email": address, "role": role, "is_demo": True})
            profiles[role] = profile
        month = timezone.localdate().replace(day=1)
        previous = (month - dt.timedelta(days=1)).replace(day=1)
        category = WorkCategory.objects.get(name="Family Support")
        for role, sheet_month, status in [("employee", month, "draft"), ("employee", previous, "approved"),
                                          ("administrator", month, "submitted")]:
            profile = profiles[role]
            sheet, created = Timesheet.objects.get_or_create(staff=profile, month=sheet_month)
            if not created:
                continue
            for offset in (0, 1, 2):
                day = sheet_month + dt.timedelta(days=offset)
                save_entry(profile.user, sheet.pk, offset, TimeEntry(timesheet=sheet, date=day, end_date=day,
                    start_time=dt.time(9), end_time=dt.time(17), break_minutes=30,
                    category=category, notes="Fictional support shift for the review demo."))
            if status != "draft":
                confirm_non_respite(sheet)
                sheet = transition(profile.user, sheet.pk, 3, "submit", notify=False)
            if status == "approved":
                transition(profiles["finance"].user, sheet.pk, sheet.revision, "approve")
        self.stdout.write("Fictional review accounts are ready. No access codes are printed or embedded in pages.")
