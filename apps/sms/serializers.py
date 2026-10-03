from rest_framework import serializers

from .models import SMSCampaign, SMSMessage, SMSTemplate


class SMSTemplateSerializer(serializers.ModelSerializer):
    placeholders = serializers.SerializerMethodField()

    class Meta:
        model = SMSTemplate
        fields = (
            "id", "name", "body", "examination", "is_default",
            "placeholders", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_placeholders(self, obj):
        return obj.placeholders()

    def create(self, validated_data):
        validated_data["school"] = self.context["request"].school
        return super().create(validated_data)


class SMSMessageSerializer(serializers.ModelSerializer):
    candidate_name = serializers.CharField(source="exam_candidate.candidate.full_name", read_only=True)
    candidate_number = serializers.CharField(source="exam_candidate.candidate_number", read_only=True)

    class Meta:
        model = SMSMessage
        fields = (
            "id", "campaign", "exam_candidate", "candidate_name", "candidate_number",
            "phone", "message", "provider", "status", "provider_message_id",
            "failure_reason", "attempts", "sent_at", "created_at",
        )
        read_only_fields = fields


class SMSCampaignSerializer(serializers.ModelSerializer):
    examination_code = serializers.CharField(source="examination.code", read_only=True)
    template_name = serializers.CharField(source="template.name", read_only=True, default=None)

    class Meta:
        model = SMSCampaign
        fields = (
            "id", "examination", "examination_code", "template", "template_name",
            "status", "total", "queued", "sent", "failed", "skipped",
            "created_by", "completed_at", "error", "created_at",
        )
        read_only_fields = fields


class SendSMSSerializer(serializers.Serializer):
    examination = serializers.UUIDField()
    template_id = serializers.UUIDField(required=False)
    candidate_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    resend_failed = serializers.BooleanField(default=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=80)
    run_async = serializers.BooleanField(default=True)
