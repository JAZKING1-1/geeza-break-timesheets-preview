"""A durable submission notice, sent after the timesheet transaction commits."""
import base64

from mailjet_rest import Client

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import SubmissionNotification
from .excel import submission_workbook, XLSX_CONTENT_TYPE


def queue_submission(sheet):
    existing = SubmissionNotification.objects.filter(timesheet=sheet, revision=sheet.revision).first()
    if existing:
        return existing
    filename, content = submission_workbook(sheet)
    review_prefix = "[Review demo] " if settings.PORTAL_PREVIEW else ""
    local_note = ("\nLocal preview: this link opens only on the computer running the website.\n"
                  if settings.PORTAL_LOCAL else "")
    url = settings.PORTAL_BASE_URL.rstrip("/") + reverse("staff:sheet", args=[sheet.pk])
    notice, created = SubmissionNotification.objects.get_or_create(
        timesheet=sheet, revision=sheet.revision,
        defaults={"recipient": settings.TIMESHEET_NOTIFICATION_EMAIL,
                  "attachment_name": filename, "attachment_content": content,
                  "subject": f"{review_prefix}Geeza Break: timesheet ready for review - {sheet.month:%B %Y}",
                  "body": (f"{sheet.staff.name} has submitted their {sheet.month:%B %Y} timesheet.\n\n"
                           f"Recorded time: {sheet.total_display}\n"
                           f"Sign in with a finance or administrator account to review it:\n{url}\n"
                           "The attached Excel file contains the entries and total hours as submitted.\n"
                           "This is a claim for review, not confirmation of approval or payment.\n"
                           f"{local_note}")})
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
        if not notice.attachment_name or not notice.attachment_content:
            # Older notices have no saved submission copy. Never attach later edits.
            raise RuntimeError("No Excel snapshot exists for this older submission")
        content = bytes(notice.attachment_content)
        if settings.TIMESHEET_EMAIL_DELIVERY == "preview":
            connection = get_connection("django.core.mail.backends.filebased.EmailBackend",
                                        file_path=settings.EMAIL_FILE_PATH)
            message = EmailMessage(notice.subject, notice.body, settings.DEFAULT_FROM_EMAIL,
                                   [notice.recipient], connection=connection)
            message.attach(notice.attachment_name, content, XLSX_CONTENT_TYPE)
            if message.send() != 1:
                raise RuntimeError("Preview could not be written")
            status, detail = "preview", "Email and Excel attachment saved in previews. No email was sent."
        elif settings.TIMESHEET_EMAIL_DELIVERY == "mailjet":
            if not all((settings.TIMESHEET_MAILJET_API_KEY, settings.TIMESHEET_MAILJET_API_SECRET)):
                raise RuntimeError("Mailjet credentials are missing")
            # Same Client / send.create workflow as the official referral mailer.
            # This independent service has only its explicitly configured recipient.
            client = Client(auth=(settings.TIMESHEET_MAILJET_API_KEY, settings.TIMESHEET_MAILJET_API_SECRET),
                            version="v3.1")
            response = client.send.create(timeout=20, data={"Messages": [{
                    "From": {"Email": settings.TIMESHEET_FROM_EMAIL, "Name": "Geeza Break Timesheets"},
                    "To": [{"Email": notice.recipient}],
                    "Subject": notice.subject, "TextPart": notice.body,
                    "Attachments": [{"ContentType": XLSX_CONTENT_TYPE,
                                     "Filename": notice.attachment_name,
                                     "Base64Content": base64.b64encode(content).decode("ascii")}],
                }]})
            response.raise_for_status()
            results = response.json().get("Messages", [])
            if len(results) != 1 or results[0].get("Status") != "success":
                raise RuntimeError("Mailjet did not accept the message")
            status, detail = "sent", "Email with Excel attachment accepted by Mailjet; inbox delivery is not yet confirmed."
        else:
            raise RuntimeError("Email delivery mode is not configured")
    except Exception:
        # Never expose provider responses, credentials or private email content.
        status, detail = "failed", "Delivery could not be confirmed. Ask the administrator to check the email settings."
    SubmissionNotification.objects.filter(pk=notice_id, status="sending").update(
        status=status, detail=detail, delivered_at=timezone.now() if status in ("sent", "preview") else None)
