from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView


def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="staff:dashboard", permanent=False)),
    path("staff/", include("staff_portal.urls")),
    path("website-review/", include("website_review.urls")),
    path("health/", health),
    path("robots.txt", lambda request: HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")),
]
