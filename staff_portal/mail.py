from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from mailjet_rest import Client


class MailjetBackend(BaseEmailBackend):
    """Account mail only: exactly the intended recipients, never a developer CC."""
    def send_messages(self, email_messages):
        sent = 0
        client = Client(auth=(settings.MAILJET_API_KEY, settings.MAILJET_API_SECRET), version="v3.1")
        for message in email_messages:
            if len(message.to) != 1 or message.cc or message.bcc:
                raise ValueError("Account mail must have exactly one recipient.")
            result = client.send.create(data={"Messages": [{
                "From": {"Email": message.from_email, "Name": "Geeza Break"},
                "To": [{"Email": message.to[0]}],
                "Subject": message.subject,
                "TextPart": message.body,
            }]})
            if result.status_code != 200:
                raise RuntimeError("Account email could not be delivered.")
            sent += 1
        return sent
