import datetime as dt
import hashlib
import logging
import secrets
from functools import wraps

import requests
from django.conf import settings
from django.contrib import auth, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import RateBucket, StaffProfile

logger = logging.getLogger(__name__)


def captcha_rejected(reason, action):
    # Never log tokens, keys, request bodies, email addresses or provider payloads.
    safe_action = action if action in {"login", "reset", "activate"} else "other"
    logger.warning("Turnstile verification rejected: reason=%s action=%s", reason, safe_action)
    return False


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def limited(scope, identifier, maximum, seconds):
    now = timezone.now()
    key = salted_hmac("staff-rate", f"{scope}:{identifier}", algorithm="sha256").hexdigest()
    with transaction.atomic():
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={
            "expires_at": now + dt.timedelta(seconds=seconds)})
        RateBucket.objects.filter(pk=bucket.pk, expires_at__lte=now).update(
            count=0, expires_at=now + dt.timedelta(seconds=seconds))
        RateBucket.objects.filter(pk=bucket.pk).update(count=F("count") + 1)
        bucket.refresh_from_db()
    return bucket.count > maximum


def request_limited(request, action, email="", maximum=10, seconds=900):
    # REMOTE_ADDR is supplied by the server, never trust arbitrary forwarded headers.
    ip_limited = limited(action + "-ip", request.META.get("REMOTE_ADDR", ""), maximum * 5, seconds)
    if ip_limited:
        return True
    account_limited = limited(action + "-account", email.strip().lower(), maximum, seconds)
    return ip_limited or account_limited


def verify_captcha(request, action):
    if settings.PORTAL_CAPTCHA_MODE == "simulate":
        return settings.PORTAL_LOCAL and request.POST.get("captcha") == "pass"
    token = request.POST.get("cf-turnstile-response", "")
    if not token:
        return captcha_rejected("missing_token", action)
    if len(token) > 2048:
        return captcha_rejected("invalid_token_length", action)
    if not settings.TURNSTILE_SECRET_KEY:
        return captcha_rejected("missing_secret", action)
    try:
        response = requests.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                                 data={"secret": settings.TURNSTILE_SECRET_KEY, "response": token},
                                 timeout=8)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        return captcha_rejected("verification_service_unavailable", action)
    if not isinstance(result, dict):
        return captcha_rejected("invalid_provider_response", action)
    if result.get("success") is not True:
        known_codes = {"missing-input-secret", "invalid-input-secret", "missing-input-response",
                       "invalid-input-response", "bad-request", "timeout-or-duplicate", "internal-error"}
        codes = result.get("error-codes")
        safe_codes = sorted({code for code in codes if isinstance(code, str) and code in known_codes}) if isinstance(codes, list) else []
        return captcha_rejected("provider_" + ("_".join(safe_codes) or "rejected"), action)
    if result.get("action") != action:
        return captcha_rejected("action_mismatch", action)
    if result.get("hostname") not in settings.TURNSTILE_HOSTNAMES:
        return captcha_rejected("hostname_mismatch", action)
    return True


def portal_required(role=None):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("staff:login")
            profile = StaffProfile.objects.filter(user=request.user, enabled=True,
                                                  user__is_active=True).first()
            if not profile or (profile.is_demo and not settings.PORTAL_PREVIEW):
                auth.logout(request)
                return redirect("staff:login")
            if role == "review" and not profile.can_review:
                raise PermissionDenied
            if role == "admin" and not profile.is_administrator:
                raise PermissionDenied
            request.staff = profile
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


class PortalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_portal = request.path.startswith("/staff/")
        if is_portal and request.user.is_authenticated:
            now = timezone.now().timestamp()
            last = request.session.get("portal_last_activity", now)
            if now - last > settings.PORTAL_IDLE_SECONDS:
                auth.logout(request)
                messages.info(request, "Your session expired. Sign in again; your saved entries are safe.")
            else:
                request.session["portal_last_activity"] = now
        response = self.get_response(request)
        if is_portal:
            response["Cache-Control"] = "no-store, private, max-age=0"
            response["Pragma"] = "no-cache"
            # Browser form POSTs need a same-origin referrer/origin for Django's
            # CSRF checks. Suppress it only on the one-use link redirect, whose
            # URL contains a bearer token and never renders a POST form.
            is_account_link = (
                request.resolver_match is not None
                and request.resolver_match.view_name == "staff:password_link"
            )
            response["Referrer-Policy"] = "no-referrer" if is_account_link else "same-origin"
            response["X-Robots-Tag"] = "noindex, nofollow"
            response["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; "
                "style-src 'self'; img-src 'self' data:; font-src 'self'; "
                "frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; "
                "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
            )
            response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
