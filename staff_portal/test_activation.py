from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import PasswordLink, StaffProfile


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, PORTAL_CAPTCHA_MODE="simulate",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StaffActivationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", "owner@example.org", is_active=False)
        self.owner = StaffProfile.objects.create(user=self.user, name="Owner", email=self.user.email,
                                                role="administrator", roster_key="portal-owner")

    def test_invited_owner_gets_setup_email_without_public_account_creation(self):
        response = self.client.post(reverse("staff:activate_account"), {"email":"OWNER@example.org", "captcha":"pass"})
        self.assertContains(response, "Check your email")
        self.assertEqual(mail.outbox[0].to, [self.owner.email])
        self.assertEqual(PasswordLink.objects.get().purpose, "setup")
        self.assertEqual(StaffProfile.objects.count(), 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        unknown = self.client.post(reverse("staff:activate_account"), {"email":"unknown@example.org", "captcha":"pass"})
        self.assertContains(unknown, "Check your email")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(StaffProfile.objects.count(), 1)

    def test_active_disabled_and_demo_accounts_cannot_be_activated(self):
        for field in ["active", "disabled", "demo"]:
            with self.subTest(field=field):
                self.user.is_active = field == "active"
                self.user.save()
                self.owner.enabled = field != "disabled"
                self.owner.is_demo = field == "demo"
                self.owner.save()
                self.client.post(reverse("staff:activate_account"), {"email":self.owner.email, "captcha":"pass"})
                self.assertFalse(PasswordLink.objects.exists())

    def test_captcha_required_and_repeated_requests_are_limited(self):
        url = reverse("staff:activate_account")
        self.client.post(url, {"email":self.owner.email, "captcha":"fail"})
        self.assertFalse(PasswordLink.objects.exists())
        for _ in range(2):
            self.client.post(url, {"email":self.owner.email, "captcha":"pass"})
        response = self.client.post(url, {"email":self.owner.email, "captcha":"pass"})
        self.assertContains(response, "Too many requests")
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(PORTAL_PREVIEW=True)
    def test_review_mode_cannot_send_account_activation(self):
        self.assertEqual(self.client.post(reverse("staff:activate_account"), {"email":self.owner.email, "captcha":"pass"}).status_code, 404)
        self.assertFalse(PasswordLink.objects.exists())


class MobileAppTests(TestCase):
    def test_manifest_is_scoped_to_staff_without_account_information(self):
        response = self.client.get(reverse("staff:manifest"))
        self.assertEqual(response.status_code, 200)
        manifest = response.json()
        self.assertEqual(manifest["scope"], "/staff/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
        self.assertNotIn("email", manifest)

    def test_worker_never_caches_staff_records_or_replays_submissions(self):
        response = self.client.get(reverse("staff:service_worker"))
        script = b"".join(response.streaming_content).decode()
        self.assertEqual(response["Service-Worker-Allowed"], "/staff/")
        self.assertIn('event.request.method !== "GET"', script)
        self.assertIn('"Cache-Control":"no-store"', script)
        self.assertNotIn("caches.", script)
        self.assertNotIn("indexedDB", script)
