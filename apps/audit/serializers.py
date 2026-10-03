from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "actor",
            "actor_email",
            "school",
            "action",
            "entity_type",
            "entity_id",
            "metadata",
            "ip_address",
            "user_agent",
            "created_at",
        )
