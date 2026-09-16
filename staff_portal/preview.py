"""Private, fictional review access. Never used in live payroll mode."""
from django import forms
from django.conf import settings
from django.contrib import auth, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .models import StaffProfile
from .security import request_limited


ROLES = [("employee", "Employee â€” enter a timesheet"),
         ("finance", "Finance â€” review and export"),
         ("administrator", "Administrator â€” view the demo team")]


class PreviewLoginForm(forms.Form):
    role = forms.ChoiceField(label="View the preview as", choices=ROLES)
    access_code = forms.CharField(label="Private review code", max_length=256,
                                 widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


@sensitive_post_parameters("access_code")
def preview_login(request):
    if not settings.PORTAL_PREVIEW:
        raise Http404
    if request.user.is_authenticated and StaffProfile.objects.filter(user=request.user, enabled=True, is_demo=True).exists():
        return redirect("staff:dashboard")
    form = PreviewLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        role = form.cleaned_data["role"]
        if request_limited(request, "preview-login", role, maximum=10):
            form.add_error(None, "Too many attempts. Please wait 15 minutes before trying again.")
        else:
            user = auth.authenticate(request, username="demo-" + role, password=form.cleaned_data["access_code"])
            if user and StaffProfile.objects.filter(user=user, is_demo=True, enabled=True).exists():
                from .auth_views import signed_in
                return signed_in(request, user)
            form.add_error("access_code", "That review code was not recognised. Check the code shared with you.")
    return render(request, "staff_portal/preview_login.html", {"form": form})


@require_POST
def switch_role(request):
    if not settings.PORTAL_PREVIEW:
        raise Http404
    if not request.user.is_authenticated or not StaffProfile.objects.filter(user=request.user, is_demo=True, enabled=True).exists():
        raise PermissionDenied
    role = request.POST.get("role")
    if role not in dict(ROLES):
        raise PermissionDenied
    profile = StaffProfile.objects.select_related("user").get(user__username="demo-" + role, is_demo=True, enabled=True)
    from .auth_views import signed_in
    signed_in(request, profile.user)
    messages.info(request, f"You are now viewing the fictional {role} account.")
    return redirect("staff:reports" if role == "finance" else "staff:staff_list" if role == "administrator" else "staff:dashboard")


class PreviewBoundaryMiddleware:
    """The shared review code only grants access to a disposable fictional demo."""
    blocked = {"staff:staff_add", "staff:staff_edit", "staff:invite", "staff:security",
               "staff:reset_password", "staff:password_link", "staff:set_password", "staff:second_factor"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if settings.PORTAL_PREVIEW and request.resolver_match.view_name in self.blocked:
            return render(request, "staff_portal/message.html", {
                "title": "Not available in this review", "intro": "The preview uses fictional accounts. Real staff access and account changes are enabled only after a separate production setup."}, status=403)
