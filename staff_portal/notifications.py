"""A durable submission notice, sent after the timesheet transaction commits."""
import requests

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import SubmissionNotification


def queue_submission(sheet):
    local_note = ("\nLocal preview: this link opens only on the computer running the website.\n"
                  if settings.PORTAL_LOCAL else "")
    url = settings.PORTAL_BASE_URL.rstrip("/") + reverse("staff:sheet", args=[sheet.pk])
    notice, created = SubmissionNotification.objects.get_or_create(
        timesheet=sheet, revision=sheet.revision,
        defaults={"recipient": settings.TIMESHEET_NOTIFICATION_EMAIL,
                  "subject": f"Geeza Break: timesheet ready for review â€” {sheet.month:%B %Y}",
                  "body": (f"{sheet.staff.name} has submitted their {sheet.month:%B %Y} timesheet.\n\n"
                           f"Recorded time: {sheet.total_display}\n"
                           f"Sign in with a finance or administrator account to review it:\n{url}\n"
                           f"{local_note}\nWork purposes and individual entries are available securely in the portal.\n")})
    if created:
        transaction.on_commit(lambda: deliver_notification(notice.pk), robust=True)
    return notice


def deliver_notification(notice_id):
    # Claim before sending: two requests or retries cannot send the same notice.
    if not SubmissionNotification.objects.filter(pk=notice_id, status="pending").update(status="sending"):
        return
    notice = SubmissionNotification.objects.get(pk=notice_id)
    try:
        validate_email(notice.recipient)
        if settings.TIMESHEET_EMAIL_DELIVERY == "preview":
            connection = get_connection("django.core.mail.backends.filebased.EmailBackend",
                                        file_path=settings.EMAIL_FILE_PATH)
            if EmailMessage(notice.subject, notice.body, settings.DEFAULT_FROM_EMAIL,
                            [notice.recipient], connection=connection).send() != 1:
                raise RuntimeError("Preview could not be written")
            status, detail = "preview", "Saved in email previews. No email was sent."
        elif settings.TIMESHEET_EMAIL_DELIVERY == "mailjet":
            if not all((settings.TIMESHEET_MAILJET_API_KEY, settings.TIMESHEET_MAILJET_API_SECRET)):
                raise RuntimeError("Mailjet credentials are missing")
            response = requests.post("https://api.mailjet.com/v3.1/send",
                auth=(settings.TIMESHEET_MAILJET_API_KEY, settings.TIMESHEET_MAILJET_API_SECRET),
                timeout=(5, 15), json={"Messages": [{
                    "From": {"Email": settings.DEFAULT_FROM_EMAIL, "Name": "Geeza Break"},
                    "To": [{"Email": notice.recipient}],
                    "Subject": notice.subject, "TextPart": notice.body,
                }]})
            response.raise_for_status()
            results = response.json().get("Messages", [])
            if len(results) != 1 or results[0].get("Status") != "success":
                raise RuntimeError("Mailjet did not accept the message")
            status, detail = "sent", "Accepted by Mailjet; inbox delivery is not yet confirmed."
        else:
            raise RuntimeError("Email delivery mode is not configured")
    except Exception:
        # Never expose provider responses, credentials or private email content.
        status, detail = "failed", "Delivery could not be confirmed. Ask the administrator to check the email settings."
    SubmissionNotification.objects.filter(pk=notice_id, status="sending").update(
        status=status, detail=detail, delivered_at=timezone.now() if status in ("sent", "preview") else None)
