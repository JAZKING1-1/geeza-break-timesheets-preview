import datetime as dt
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower


class StaffProfile(models.Model):
    class Role(models.TextChoices):
        EMPLOYEE = "employee", "Employee"
        FINANCE = "finance", "Finance"
        ADMIN = "administrator", "Administrator"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.PROTECT, related_name="staff_profile")
    name = models.CharField(max_length=120)
    job_title = models.CharField(max_length=160)
    email = models.EmailField(null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    enabled = models.BooleanField(default=True)
    is_demo = models.BooleanField(default=False)
    roster_key = models.CharField(max_length=80, null=True, blank=True, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("email"), name="staff_email_unique_ci")]

    def __str__(self):
        return self.name

    @property
    def can_review(self):
        return self.role in (self.Role.FINANCE, self.Role.ADMIN)

    @property
    def is_administrator(self):
        return self.role == self.Role.ADMIN

    @property
    def account_status(self):
        if not self.enabled:
            return "Disabled"
        if self.user_id and self.user.is_active:
            return "Active"
        if self.email and self.user_id:
            return "Invited"
        return "Awaiting email"

    def clean(self):
        self.email = self.email.strip().lower() if self.email else None


class WorkCategory(models.Model):
    name = models.CharField(max_length=60, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Timesheet(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        RETURNED = "returned", "Returned"
        APPROVED = "approved", "Approved"

    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="timesheets")
    month = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    review_note = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month", "staff__name"]
        constraints = [models.UniqueConstraint(fields=["staff", "month"], name="one_sheet_per_month")]

    @property
    def editable(self):
        return self.status in (self.Status.DRAFT, self.Status.RETURNED)

    @property
    def total_minutes(self):
        return sum(entry.minutes for entry in self.entries.all())

    @property
    def total_display(self):
        minutes = self.total_minutes
        return f"{minutes // 60}h {minutes % 60:02d}m"


def london_datetime(day, clock, fold=0):
    naive = dt.datetime.combine(day, clock)
    aware = naive.replace(tzinfo=ZoneInfo("Europe/London"), fold=fold)
    back = aware.astimezone(dt.timezone.utc).astimezone(aware.tzinfo)
    if back.replace(tzinfo=None) != naive or back.fold != fold:
        raise ValidationError("This time does not exist in the UK clock-change calendar. Check the time and occurrence.")
    return aware


class TimeEntry(models.Model):
    timesheet = models.ForeignKey(Timesheet, on_delete=models.CASCADE, related_name="entries")
    date = models.DateField()
    start_time = models.TimeField()
    end_date = models.DateField()
    end_time = models.TimeField()
    start_fold = models.PositiveSmallIntegerField(default=0, choices=[(0, "First occurrence (usual)"), (1, "Second occurrence after clocks go back")])
    end_fold = models.PositiveSmallIntegerField(default=0, choices=[(0, "First occurrence (usual)"), (1, "Second occurrence after clocks go back")])
    break_minutes = models.PositiveIntegerField(default=0)
    category = models.ForeignKey(WorkCategory, on_delete=models.PROTECT)
    notes = models.CharField(max_length=500, blank=True)
    minutes = models.PositiveIntegerField(editable=False)
    starts_at = models.DateTimeField(editable=False)
    ends_at = models.DateTimeField(editable=False)

    class Meta:
        ordering = ["date", "start_time"]

    def clean(self):
        self.clean_timing()
        if self.starts_at and self.ends_at:
            self.check_overlap()

    def check_overlap(self, exclude_sheet=False):
        entries = TimeEntry.objects.filter(timesheet__staff_id=self.timesheet.staff_id,
                                            starts_at__lt=self.ends_at, ends_at__gt=self.starts_at)
        entries = entries.exclude(timesheet_id=self.timesheet_id) if exclude_sheet else entries.exclude(pk=self.pk)
        if entries.exists():
            raise ValidationError("This shift overlaps another entry. Check the start and end times.")

    def clean_timing(self):
        if not all((self.date, self.start_time, self.end_date, self.end_time, self.timesheet_id)):
            return
        start = london_datetime(self.date, self.start_time, self.start_fold).astimezone(dt.timezone.utc)
        end = london_datetime(self.end_date, self.end_time, self.end_fold).astimezone(dt.timezone.utc)
        duration = int((end - start).total_seconds() // 60)
        if duration <= 0 or duration > 24 * 60:
            raise ValidationError("A shift must be longer than zero and no longer than 24 hours.")
        # Midnight ending on the next month belongs to the preceding day.
        last_work_day = (end - dt.timedelta(seconds=1)).astimezone(ZoneInfo("Europe/London")).date()
        month = self.timesheet.month
        if self.date.replace(day=1) != month or last_work_day.replace(day=1) != month:
            raise ValidationError("Split shifts at midnight when they cross into another month.")
        if self.break_minutes is None or self.break_minutes < 0 or self.break_minutes >= duration:
            raise ValidationError("Unpaid breaks must be shorter than the shift.")
        self.starts_at, self.ends_at = start, end
        self.minutes = duration - self.break_minutes


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    staff = models.ForeignKey(StaffProfile, null=True, on_delete=models.PROTECT)
    timesheet = models.ForeignKey(Timesheet, null=True, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=80)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class PasswordLink(models.Model):
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=10, choices=[("setup", "Setup"), ("reset", "Reset")])
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=128)


class RateBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()


class SubmissionNotification(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting to send"
        SENDING = "sending", "Delivery in progress"
        PREVIEW = "preview", "Local preview only — not emailed"
        SENT = "sent", "Accepted by Mailjet"
        FAILED = "failed", "Email delivery needs attention"

    timesheet = models.ForeignKey(Timesheet, on_delete=models.PROTECT, related_name="notifications")
    revision = models.PositiveIntegerField()
    recipient = models.EmailField(blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    attachment_name = models.CharField(max_length=160, blank=True)
    attachment_content = models.BinaryField(default=bytes, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["timesheet", "revision"], name="one_notice_per_submission")]
