from django.db import models

from apps.core.models import UUIDModel

ALLOWED_PLACEHOLDERS = {
    "candidate_name",
    "candidate_number",
    "position",
    "school_position",
    "exam_name",
    "school_name",
    "average",
    "grade",
    "division",
    "total",
}

DEFAULT_SMS_BODY = "EMAS: Mwanao {candidate_name} amepata nafasi ya {position} katika {exam_name}."


class SMSTemplate(UUIDModel):
    """Configurable result-notification template. ``examination`` null =
    organizer-school default."""

    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="sms_templates",
        null=True, blank=True,
    )
    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, null=True, blank=True,
        related_name="sms_templates",
    )
    name = models.CharField(max_length=100)
    body = models.TextField(default=DEFAULT_SMS_BODY)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)

    def placeholders(self):
        import string

        return [f for _, f, _, _ in string.Formatter().parse(self.body) if f]

    def clean(self):
        from django.core.exceptions import ValidationError

        unknown = set(self.placeholders()) - ALLOWED_PLACEHOLDERS
        if unknown:
            raise ValidationError(
                {"body": f"Unsupported placeholders: {', '.join(sorted(unknown))}."}
            )

    def render(self, context):
        safe = {k: context.get(k, "") for k in self.placeholders()}
        return self.body.format(**safe)

    def __str__(self):
        return self.name


class SMSCampaign(UUIDModel):
    """One batch SMS sending operation for a published examination."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="sms_campaigns"
    )
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="+"
    )
    template = models.ForeignKey(SMSTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.QUEUED)
    scope = models.JSONField(
        default=dict,
        blank=True,
        help_text="{'candidates': [ids]} for selected candidates; {} = all.",
    )
    idempotency_key = models.CharField(max_length=80, blank=True)
    total = models.PositiveIntegerField(default=0)
    queued = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"SMS {self.examination.code} ({self.status})"


class SMSMessage(UUIDModel):
    """One SMS destined for one guardian/candidate phone."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"

    campaign = models.ForeignKey(SMSCampaign, on_delete=models.CASCADE, related_name="messages")
    exam_candidate = models.ForeignKey(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE, related_name="sms_messages"
    )
    phone = models.CharField(max_length=20)
    message = models.TextField()
    provider = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    provider_message_id = models.CharField(max_length=120, blank=True)
    failure_reason = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "exam_candidate"),
                name="unique_sms_per_campaign_candidate",
            )
        ]
        indexes = [models.Index(fields=("campaign", "status"))]

    def __str__(self):
        return f"SMS->{self.phone} ({self.status})"
