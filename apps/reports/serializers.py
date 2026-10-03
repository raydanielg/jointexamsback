from rest_framework import serializers

from .models import ReportJob, ReportTemplate, ReportType


class ReportTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportTemplate
        fields = (
            "id", "name", "report_type", "header", "footer", "show_logo",
            "show_positions", "show_division", "show_comments", "signature_labels",
            "is_default", "created_at",
        )
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        validated_data["school"] = self.context["request"].school
        return super().create(validated_data)


class ReportJobSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    examination_code = serializers.CharField(source="examination.code", read_only=True)
    requested_by_email = serializers.EmailField(source="requested_by.email", read_only=True, default=None)

    class Meta:
        model = ReportJob
        fields = (
            "id", "examination", "examination_code", "report_type", "format",
            "params", "status", "file", "file_url", "error", "requested_by",
            "requested_by_email", "completed_at", "created_at",
        )
        read_only_fields = fields

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get("request")
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        return None


class CreateReportSerializer(serializers.Serializer):
    examination = serializers.UUIDField()
    report_type = serializers.ChoiceField(choices=ReportType.choices)
    format = serializers.ChoiceField(choices=ReportJob.Format.choices, default=ReportJob.Format.PDF)
    params = serializers.DictField(required=False)
    run_async = serializers.BooleanField(default=True)
