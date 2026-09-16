from django.conf import settings


def portal_context(request):
    return {
        "portal_local": settings.PORTAL_LOCAL,
        "portal_preview": settings.PORTAL_PREVIEW,
        "captcha_simulated": settings.PORTAL_CAPTCHA_MODE == "simulate",
        "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
        "portal_staff": getattr(request, "staff", None),
        "notification_preview": settings.TIMESHEET_EMAIL_DELIVERY == "preview",
    }
