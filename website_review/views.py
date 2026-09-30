"""Read-only website content, independent of staff records and payroll workflows."""
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .content import ADRS_COPY, BRIGHTER_BIO, TESTIMONIALS


def review_page(request, page, title, **context):
    response = render(request, f"website_review/{page}.html", {
        "page": page, "title": title, **context,
    })
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; object-src 'none'; "
        "base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
    )
    response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@require_safe
def services(request):
    return review_page(request, "services", "Our services")


@require_safe
def brighter_beginnings(request):
    return review_page(request, "brighter_beginnings", "Brighter Beginnings",
                       bio=BRIGHTER_BIO, testimonials=TESTIMONIALS)


@require_safe
def adrs(request):
    return review_page(request, "adrs", "ADRS referrals for Respite Sitting Service", copy=ADRS_COPY)


@require_safe
def groups(request):
    return review_page(request, "groups", "Family Support Group Work")
