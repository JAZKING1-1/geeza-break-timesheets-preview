from django.urls import path

from . import views

app_name = "website_review"

urlpatterns = [
    path("", views.services, name="services"),
    path("brighter-beginnings/", views.brighter_beginnings, name="brighter_beginnings"),
    path("adrs-respite-sitting/", views.adrs, name="adrs"),
    path("family-support-group-work/", views.groups, name="groups"),
]
