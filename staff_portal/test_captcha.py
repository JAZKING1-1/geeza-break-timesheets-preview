from unittest.mock import Mock, patch

import requests
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .security import verify_captcha


@override_settings(PORTAL_CAPTCHA_MODE="turnstile", TURNSTILE_SECRET_KEY="private-test-key",
                   TURNSTILE_HOSTNAMES=["staff.example.test"])
class CaptchaVerificationTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().post("/staff/login/", {
            "cf-turnstile-response": "private-test-token", "password": "private-test-password"})

    def test_success_still_requires_matching_action_and_hostname(self):
        for changes, expected, reason in [({}, True, None), ({"action": "reset"}, False, "action_mismatch"),
                                         ({"hostname": "other.test"}, False, "hostname_mismatch")]:
            result = {"success": True, "action": "login", "hostname": "staff.example.test", **changes}
            with self.subTest(changes=changes), patch("staff_portal.security.requests.post", return_value=Mock(json=lambda:result)):
                if expected:
                    self.assertTrue(verify_captcha(self.request, "login"))
                else:
                    with self.assertLogs("staff_portal.security") as logs:
                        self.assertFalse(verify_captcha(self.request, "login"))
                    self.assertIn(reason, logs.output[0])

    def test_diagnostic_logs_do_not_include_credentials_or_untrusted_error_codes(self):
        result = {"success": False, "error-codes": ["timeout-or-duplicate", "private-test-token", {"invalid":1}]}
        with patch("staff_portal.security.requests.post", return_value=Mock(json=lambda:result)):
            with self.assertLogs("staff_portal.security") as logs:
                self.assertFalse(verify_captcha(self.request, "login"))
        self.assertIn("provider_timeout-or-duplicate", logs.output[0])
        for value in ("private-test-key", "private-test-token", "private-test-password"):
            self.assertNotIn(value, "".join(logs.output))

    def test_missing_and_overlong_tokens_never_reach_provider(self):
        for value, reason in [("", "missing_token"), ("x"*2049, "invalid_token_length")]:
            with self.subTest(reason=reason), patch("staff_portal.security.requests.post") as post:
                request = RequestFactory().post("/staff/login/", {"cf-turnstile-response":value})
                with self.assertLogs("staff_portal.security") as logs:
                    self.assertFalse(verify_captcha(request, "login"))
                self.assertIn(reason, logs.output[0])
                post.assert_not_called()

    def test_network_and_malformed_responses_fail_closed(self):
        for result in ([], None, {"success":False, "error-codes":None}):
            with self.subTest(result=result), patch("staff_portal.security.requests.post", return_value=Mock(json=lambda:result)):
                with self.assertLogs("staff_portal.security"):
                    self.assertFalse(verify_captcha(self.request, "login"))
        with patch("staff_portal.security.requests.post", side_effect=requests.Timeout("private-test-key")):
            with self.assertLogs("staff_portal.security") as logs:
                self.assertFalse(verify_captcha(self.request, "login"))
        self.assertIn("verification_service_unavailable", logs.output[0])
        self.assertNotIn("private-test-key", logs.output[0])


@override_settings(PORTAL_PREVIEW=False, PORTAL_CAPTCHA_MODE="turnstile", TURNSTILE_SITE_KEY="public-test-key")
class CaptchaFormTests(TestCase):
    def test_auth_forms_wait_for_verification_and_use_correct_action(self):
        for route, action in [("login", "login"), ("activate_account", "activate"), ("reset_password", "reset")]:
            with self.subTest(route=route):
                response = self.client.get(reverse("staff:"+route))
                self.assertContains(response, 'disabled data-captcha-submit')
                self.assertContains(response, 'data-action="'+action+'"')
                self.assertContains(response, 'captcha-status')
                self.assertContains(response, 'captcha.js')
                self.assertContains(response, 'render=explicit')

    @override_settings(PORTAL_CAPTCHA_MODE="simulate")
    def test_local_simulator_remains_independent_of_cloudflare(self):
        response = self.client.get(reverse("staff:login"))
        self.assertNotContains(response, 'disabled data-captcha-submit')
        self.assertNotContains(response, 'turnstile/v0/api.js')
