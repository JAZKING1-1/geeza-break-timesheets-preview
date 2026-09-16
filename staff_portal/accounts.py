import datetime as dt
import secrets
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import PasswordLink, RecoveryCode, StaffProfile
from .security import token_digest
from .services import audit


@transaction.atomic
def issue_link(profile, purpose, actor=None):
    profile = StaffProfile.objects.select_for_update().get(pk=profile.pk)
    if not profile.email or not profile.enabled:
        raise ValidationError("Add a confirmed email and enable this staff profile first.")
    if not profile.user_id:
        user = get_user_model()(username="staff-" + uuid.uuid4().hex, email=profile.email, is_active=False)
        user.set_unusable_password()
        user.save()
        profile.user = user
        profile.save(update_fields=["user"])
    if purpose == "setup" and profile.user.is_active:
        raise ValidationError("This account is active. Use password recovery instead.")
    PasswordLink.objects.filter(staff=profile, used_at__isnull=True).update(used_at=timezone.now())
    token = secrets.token_urlsafe(32)
    link = PasswordLink.objects.create(staff=profile, token_hash=token_digest(token), purpose=purpose,
                                      expires_at=timezone.now() + dt.timedelta(hours=24 if purpose == "setup" else 1))
    url = settings.PORTAL_BASE_URL.rstrip("/") + reverse("staff:password_link", args=[token])
    body = (f"Hello {profile.name},\n\n"
            f"Use this personal link to {'set' if purpose == 'setup' else 'reset'} your Geeza Break password:\n\n"
            f"{url}\n\nThis link works once and expires in {'24 hours' if purpose == 'setup' else 'one hour'}.\n"
            "If you did not expect this email, you can ignore it. Never share this link.\n\nGeeza Break")
    send_mail("Set your Geeza Break password" if purpose == "setup" else "Reset your Geeza Break password",
              body, settings.DEFAULT_FROM_EMAIL, [profile.email], fail_silently=False)
    audit(actor, "Invitation issued" if purpose == "setup" else "Password reset requested", profile)
    return link


@transaction.atomic
def consume_link(link_id, digest, password):
    link = PasswordLink.objects.select_for_update().select_related("staff__user").filter(
        pk=link_id, token_hash=digest, used_at__isnull=True, expires_at__gt=timezone.now(),
        staff__enabled=True).first()
    if not link:
        raise ValidationError("This link has expired or has already been used. Request a new one.")
    if PasswordLink.objects.filter(pk=link.pk, used_at__isnull=True).update(used_at=timezone.now()) != 1:
        raise ValidationError("This link has already been used.")
    user = link.staff.user
    from django.contrib.auth.password_validation import validate_password
    validate_password(password, user)
    user.set_password(password)
    user.is_active = True
    user.save(update_fields=["password", "is_active"])
    PasswordLink.objects.filter(staff=link.staff, used_at__isnull=True).update(used_at=timezone.now())
    audit(user, "Password set" if link.purpose == "setup" else "Password reset", link.staff)
    return user


@transaction.atomic
def verify_second_factor(user, code):
    code = code.strip().replace(" ", "")
    device = TOTPDevice.objects.select_for_update().filter(user=user, confirmed=True).first()
    if not device:
        return False
    if device.verify_token(code):
        return True
    for recovery in RecoveryCode.objects.select_for_update().filter(user=user):
        if check_password(code, recovery.code_hash):
            deleted, _ = RecoveryCode.objects.filter(pk=recovery.pk).delete()
            return bool(deleted)
    return False


def new_recovery_codes(user):
    RecoveryCode.objects.filter(user=user).delete()
    codes = [secrets.token_hex(6) for _ in range(8)]
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, code_hash=make_password(code)) for code in codes])
    return codes
