from django.core.management.base import BaseCommand

from staff_portal.models import SubmissionNotification
from staff_portal.notifications import deliver_notification


class Command(BaseCommand):
    help = "Process pending submission notices after an interrupted web request. Does not resend completed or uncertain deliveries."

    def handle(self, *args, **options):
        ids = list(SubmissionNotification.objects.filter(status="pending").values_list("pk", flat=True))
        for notice_id in ids:
            deliver_notification(notice_id)
        self.stdout.write(f"Processed {len(ids)} pending notification(s).")
