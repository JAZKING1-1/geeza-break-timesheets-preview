"""Public app metadata; no staff data is cached for offline use."""
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.http import require_GET


@require_GET
def manifest(request):
    return JsonResponse({
        "id": "/staff/", "name": "Geeza Break Staff", "short_name": "Geeza Break",
        "description": "Record your work and submit your timesheets to Geeza Break.",
        "start_url": "/staff/", "scope": "/staff/", "display": "standalone",
        "background_color": "#f1fbfc", "theme_color": "#00789a", "lang": "en-GB",
        "icons": [{"src": static(f"staff_portal/app-icon-{size}.png"),
                   "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
                  for size in [192, 512]],
    }, content_type="application/manifest+json")


@require_GET
def service_worker(request):
    path = Path(settings.BASE_DIR) / "staff_portal/static/staff_portal/sw.js"
    response = FileResponse(path.open("rb"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/staff/"
    return response
