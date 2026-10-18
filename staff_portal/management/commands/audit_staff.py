"""Read-only roster check. Never outputs email addresses or authentication data."""
import json

from django.core.management.base import BaseCommand

from staff_portal.models import StaffProfile


class Command(BaseCommand):
    help = "List staff names, roles and account readiness without changing records."

    def handle(self, *args, **options):
        for profile in StaffProfile.objects.select_related("user").filter(is_demo=False):
            self.stdout.write("STAFF_ROSTER_AUDIT " + json.dumps({
                "name": profile.name, "job_title": profile.job_title,
                "role": profile.role, "status": profile.account_status,
                "has_email": bool(profile.email), "has_account": bool(profile.user_id),
            }))
