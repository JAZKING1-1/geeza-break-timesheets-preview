"""Service names checked against Geeza Break's public pages on 21 September 2026."""
from .models import WorkCategory

SERVICES = (
    "Family Support", "Respite Sitting", "Respite Care", "Geeza Chance",
    "Family Support Group Work", "Kinship Care",
)
INTERNAL_WORK = ("Administration", "Project Work", "Training", "Other")


def sync_service_catalog():
    for name in SERVICES + INTERNAL_WORK:
        WorkCategory.objects.get_or_create(name=name)
    # Retain the original label on historical shifts; offer specific services for new work.
    WorkCategory.objects.filter(name="Respite").update(active=False)
