import base64
import io
import logging

import qrcode
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from .accounts import consume_link, issue_link, new_recovery_codes, verify_second_factor
from .forms import CodeForm, LoginForm, PortalPasswordForm, ResetForm, SecurityForm
from .models import PasswordLink, RecoveryCode, StaffProfile
from .security import portal_required, request_limited, token_digest, verify_captcha
from .services import audit

logger = logging.getLogger(__name__)


def signed_in(request, user):
    auth.login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["portal_last_activity"] = timezone.now().timestamp()
    request.session.pop("pending_user", None)
    request.session.pop("pending_until", None)
    audit(user, "Signed in", user.staff_profile)
    return redirect("staff:reports" if user.staff_profile.role == StaffProfile.Role.FINANCE else "staff:dashboard")


@sensitive_post_parameters("password")
def login(request):
    if settings.PORTAL_PREVIEW:
        from .preview import preview_login
        return preview_login(request)
    if request.user.is_authenticated and StaffProfile.objects.filter(user=request.user, enabled=True).exists():
        return redirect("staff:reports" if request.user.staff_profile.role == StaffProfile.Role.FINANCE else "staff:dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        if request_limited(request, "login", email):
            form.add_error(None, "Too many attempts. Please wait 15 minutes and try again.")
        elif not verify_captcha(request, "login"):
            form.add_error(None, "Verification was unsuccessful on the previous attempt. Complete the new security check below, re-enter your password and try again.")
        else:
            profile = StaffProfile.objects.select_related("user").filter(email__iexact=email, enabled=True, is_demo=False).first()
            user = None
            if profile and profile.user_id:
                user = auth.authenticate(request, username=profile.user.username, password=form.cleaned_data["password"])
            else:
                get_user_model()().set_password(form.cleaned_data["password"])
            if user:
                if TOTPDevice.objects.filter(user=user, confirmed=True).exists():
                    request.session.cycle_key()
                    request.session["pending_user"] = user.pk
                    request.session["pending_until"] = timezone.now().timestamp() + 300
                    return redirect("staff:second_factor")
                return signed_in(request, user)
            form.add_error(None, "We couldn’t sign you in. Check your email and password.")
    return render(request, "staff_portal/login.html", {"form": form, "captcha_action": "login"})


@require_POST
def logout(request):
    if request.user.is_authenticated:
        audit(request.user, "Signed out", getattr(request.user, "staff_profile", None))
    auth.logout(request)
    return redirect("staff:login")


@sensitive_post_parameters("code")
def second_factor(request):
    profile = StaffProfile.objects.select_related("user").filter(
        user_id=request.session.get("pending_user"), enabled=True, user__is_active=True).first()
    if not profile or timezone.now().timestamp() > request.session.get("pending_until", 0):
        request.session.pop("pending_user", None)
        return redirect("staff:login")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if request_limited(request, "mfa", str(profile.pk), maximum=8):
            form.add_error(None, "Too many attempts. Wait 15 minutes, then sign in again.")
        elif verify_second_factor(profile.user, form.cleaned_data["code"]):
            return signed_in(request, profile.user)
        else:
            form.add_error("code", "That code could not be verified. Try a fresh code or an unused recovery code.")
    return render(request, "staff_portal/auth_form.html", {
        "form": form, "title": "One more step", "intro": "Enter your authenticator code or one of your saved recovery codes.",
        "button": "Verify and sign in"})


def activate_account(request):
    """Invite-only signup: the address must already be on the authorised staff roster."""
    if settings.PORTAL_PREVIEW:
        raise Http404
    form = ResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        if request_limited(request, "activate", email, maximum=3, seconds=3600):
            form.add_error(None, "Too many requests. Please try again in an hour.")
        elif not verify_captcha(request, "activate"):
            form.add_error(None, "Verification was unsuccessful. Please try again.")
        else:
            profile = StaffProfile.objects.filter(
                Q(user__is_active=False) | Q(user__isnull=True),
                email__iexact=email, enabled=True, is_demo=False).first()
            if profile:
                try:
                    issue_link(profile, "setup")
                except Exception:
                    logger.warning("Account activation email could not be delivered.")
            return render(request, "staff_portal/message.html", {
                "title": "Check your email",
                "intro": "If this address is registered and needs activation, we have sent a personal setup link. It works once and expires in 24 hours. If you already have a password, sign in or use password recovery."})
    return render(request, "staff_portal/auth_form.html", {
        "form": form, "title": "Set up your staff account",
        "intro": "Enter the work email your administrator has registered. We will email you a private link to choose your password.",
        "button": "Email my setup link", "captcha_action": "activate"})


def reset_password(request):
    form = ResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        if request_limited(request, "reset", email, maximum=5, seconds=3600):
            form.add_error(None, "Too many requests. Please try again in an hour.")
        elif not verify_captcha(request, "reset"):
            form.add_error(None, "Verification was unsuccessful. Please try again.")
        else:
            profile = StaffProfile.objects.select_related("user").filter(
                email__iexact=email, enabled=True, is_demo=False).first()
            if profile:
                try:
                    purpose = "reset" if profile.user_id and profile.user.is_active else "setup"
                    issue_link(profile, purpose)
                except Exception:
                    logger.warning("Password recovery email could not be delivered.")
            return render(request, "staff_portal/message.html", {
                "title": "Check your email", "intro": "If this email is registered for staff access, we have sent a private link to set or reset your password. Check your inbox and junk folder. The email tells you when the link expires."})
    return render(request, "staff_portal/auth_form.html", {
        "form": form, "title": "Set or reset your password", "intro": "Enter your registered work email. This also works if you have not set a password yet.",
        "button": "Email my password link", "captcha_action": "reset"})


def password_link(request, token):
    link = PasswordLink.objects.filter(token_hash=token_digest(token), used_at__isnull=True,
                                       expires_at__gt=timezone.now(), staff__enabled=True).first()
    if not link:
        return render(request, "staff_portal/message.html", {
            "title": "This link is no longer available", "intro": "It may have expired or been used already. Request a password reset, or ask your administrator for another invitation."}, status=400)
    request.session["password_link_id"] = link.pk
    request.session["password_link_digest"] = link.token_hash
    return redirect("staff:set_password")


@sensitive_post_parameters("new_password1", "new_password2")
def set_password(request):
    link = PasswordLink.objects.select_related("staff__user").filter(
        pk=request.session.get("password_link_id"), token_hash=request.session.get("password_link_digest", ""),
        used_at__isnull=True, expires_at__gt=timezone.now(), staff__enabled=True).first()
    if not link:
        return render(request, "staff_portal/message.html", {
            "title": "Request a new link", "intro": "Open the latest link from your invitation or password reset email."}, status=400)
    form = PortalPasswordForm(link.staff.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        if request_limited(request, "set-password", str(link.pk)):
            form.add_error(None, "Too many attempts. Please try again later.")
        else:
            try:
                consume_link(link.pk, link.token_hash, form.cleaned_data["new_password1"])
            except ValidationError as exc:
                form.add_error(None, exc)
            except OperationalError:
                form.add_error(None, "Another account change is in progress. Please try again.")
            else:
                auth.logout(request)
                messages.success(request, "Your password is ready. You can now sign in.")
                return redirect("staff:login")
    return render(request, "staff_portal/auth_form.html", {
        "form": form, "title": "Set up your staff account", "intro": f"Choose a personal password for {link.staff.name}.",
        "button": "Save my password"})


@portal_required()
@sensitive_post_parameters("password", "code")
def security_settings(request):
    form = SecurityForm(request.POST or None)
    codes = None
    device = TOTPDevice.objects.filter(user=request.user).first()
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action")
        if request_limited(request, "security", str(request.user.pk), maximum=8):
            form.add_error(None, "Too many attempts. Please try again in 15 minutes.")
        elif not request.user.check_password(form.cleaned_data["password"]):
            form.add_error("password", "Check your current password.")
        else:
            with transaction.atomic():
                # Lock the user, so parallel enrolment cannot create multiple devices.
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                device = TOTPDevice.objects.select_for_update().filter(user=request.user).first()
                if action == "begin" and not device:
                    device = TOTPDevice.objects.create(user=request.user, name="Authenticator", confirmed=False)
                    audit(request.user, "Authenticator setup started", request.staff)
                    form = SecurityForm()
                elif action == "confirm" and device and not device.confirmed:
                    if device.verify_token(form.cleaned_data["code"]):
                        device.confirmed = True
                        device.save(update_fields=["confirmed"])
                        codes = new_recovery_codes(request.user)
                        audit(request.user, "Authenticator enabled", request.staff)
                        messages.success(request, "Authenticator protection is now on. Save your recovery codes.")
                        form = SecurityForm()
                    else:
                        form.add_error("code", "Enter a fresh code from the authenticator app.")
                elif action in ("disable", "regenerate") and device and device.confirmed:
                    if verify_second_factor(request.user, form.cleaned_data["code"]):
                        if action == "disable":
                            device.delete()
                            device = None
                            RecoveryCode.objects.filter(user=request.user).delete()
                            audit(request.user, "Authenticator disabled", request.staff)
                            messages.success(request, "Authenticator protection is now off.")
                        else:
                            codes = new_recovery_codes(request.user)
                            audit(request.user, "Recovery codes replaced", request.staff)
                        form = SecurityForm()
                    else:
                        form.add_error("code", "Verify with your authenticator or an unused recovery code.")
                elif action == "cancel" and device and not device.confirmed:
                    device.delete()
                    device = None
                    form = SecurityForm()
                else:
                    form.add_error(None, "This security action is no longer available. Reload the page.")
    qr = None
    if device and not device.confirmed:
        buffer = io.BytesIO()
        qrcode.make(device.config_url).save(buffer, format="PNG")
        qr = base64.b64encode(buffer.getvalue()).decode()
    return render(request, "staff_portal/security.html", {
        "form": form, "device": device, "qr": qr, "codes": codes,
        "manual_key": base64.b32encode(device.bin_key).decode() if device and not device.confirmed else None})
