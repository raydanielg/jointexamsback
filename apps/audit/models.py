from django.db import models


class AuditLog(models.Model):
    """Centralized, append-only audit trail for security-relevant events."""

    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    school = models.ForeignKey(
        "schools.School", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80, db_index=True)
    entity_type = models.CharField(max_length=80, db_index=True)
    entity_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("school", "created_at")),
            models.Index(fields=("entity_type", "entity_id")),
            models.Index(fields=("actor", "created_at")),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id} by {self.actor}"
