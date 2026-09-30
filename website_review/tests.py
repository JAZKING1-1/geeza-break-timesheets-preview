from django.test import SimpleTestCase, override_settings
from django.urls import reverse


@override_settings(PORTAL_PREVIEW=False, PORTAL_LOCAL=True, SECURE_SSL_REDIRECT=False)
class WebsiteReviewBoundaryTests(SimpleTestCase):
    def test_review_is_database_free_read_only_and_not_indexable(self):
        # SimpleTestCase rejects database access: public content must never need staff records.
        for page in ("services", "brighter_beginnings", "adrs", "groups"):
            with self.subTest(page=page):
                url = reverse(f"website_review:{page}")
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("noindex", response["X-Robots-Tag"])
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertIn("form-action 'none'", response["Content-Security-Policy"])
                self.assertNotContains(response, "<form")
                self.assertEqual(self.client.post(url, {"email": "nobody@example.test"}).status_code, 405)

    def test_viewing_public_pages_does_not_grant_staff_access(self):
        self.client.get(reverse("website_review:services"))
        for page in ("dashboard", "new_sheet", "reports", "staff_list"):
            with self.subTest(page=page):
                response = self.client.get(reverse(f"staff:{page}"))
                self.assertRedirects(response, reverse("staff:login"), fetch_redirect_response=False)
