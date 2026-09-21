from django import forms
from django.contrib.auth.forms import SetPasswordForm
from django.core.validators import MaxLengthValidator
from .models import StaffProfile, TimeEntry, WorkCategory


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(max_length=256, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class ResetForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class PortalPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["autocomplete"] = "new-password"
            field.max_length = 256
            field.validators.append(MaxLengthValidator(256))
            field.widget.attrs["maxlength"] = 256
        self.fields["new_password1"].help_text = "Use a unique passphrase of at least 15 characters. Spaces are welcome."


class MonthForm(forms.Form):
    month = forms.DateField(input_formats=["%Y-%m"], widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}))


class YearForm(forms.Form):
    year = forms.IntegerField(label="Calendar year", min_value=1, max_value=9999,
                              widget=forms.NumberInput(attrs={"inputmode": "numeric"}))


class EntryForm(forms.ModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput())
    class Meta:
        model = TimeEntry
        fields = ["date", "start_time", "end_date", "end_time", "break_minutes",
                  "category", "notes", "start_fold", "end_fold"]
        widgets = {
            "date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "break_minutes": forms.NumberInput(attrs={"min": 0, "max": 1440}),
            "notes": forms.Textarea(attrs={"rows": 3, "maxlength": 500}),
        }
        labels = {"date": "Start date", "break_minutes": "Unpaid break (minutes)",
                  "category": "Service / work type", "start_fold": "Start time occurrence",
                  "end_fold": "End time occurrence"}
        help_texts = {"notes": "Optional. Do not include family names, addresses or sensitive care details.",
                      "end_date": "Usually the start date. Change this for an overnight shift."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = WorkCategory.objects.filter(active=True)
        self.fields["start_time"].input_formats = ["%H:%M"]
        self.fields["end_time"].input_formats = ["%H:%M"]


class ProfileForm(forms.ModelForm):
    class Meta:
        model = StaffProfile
        fields = ["name", "job_title", "email", "role", "enabled"]
        labels = {"email": "Confirmed staff email", "enabled": "Allow access"}
        help_texts = {"email": "Leave blank until confirmed. Changing an address requires a new invitation.",
                      "role": "Job titles do not grant system permissions."}


class CodeForm(forms.Form):
    code = forms.CharField(max_length=40, label="Authenticator or recovery code",
                          widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "autofocus": True}))


class SecurityForm(forms.Form):
    password = forms.CharField(max_length=256, label="Current password",
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    code = forms.CharField(max_length=40, required=False, label="Authenticator or recovery code",
                          widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}))
