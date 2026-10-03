from rest_framework import serializers

from .models import ImportSession


class ImportSessionSerializer(serializers.ModelSerializer):
    error_report_url = serializers.SerializerMethodField()
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default=None)

    class Meta:
        model = ImportSession
        fields = (
            "id",
            "import_type",
            "status",
            "file",
            "params",
            "strict",
            "total_rows",
            "success_rows",
            "failed_rows",
            "duplicate_rows",
            "updated_rows",
            "skipped_rows",
            "errors",
            "error_report",
            "error_report_url",
            "created_by",
            "created_by_email",
            "completed_at",
            "created_at",
        )
        read_only_fields = (
            "id", "status", "total_rows", "success_rows", "failed_rows", "duplicate_rows",
            "updated_rows", "skipped_rows", "errors", "error_report", "created_by",
            "completed_at", "created_at",
        )

    def get_error_report_url(self, obj):
        if obj.error_report:
            request = self.context.get("request")
            return request.build_absolute_uri(obj.error_report.url) if request else obj.error_report.url
        return None


class ImportUploadSerializer(serializers.Serializer):
    import_type = serializers.ChoiceField(choices=ImportSession.ImportType.choices)
    file = serializers.FileField()
    strict = serializers.BooleanField(default=True)
    candidate_list = serializers.UUIDField(required=False)
    examination = serializers.UUIDField(required=False)
    center = serializers.UUIDField(required=False)
    run_async = serializers.BooleanField(default=True)
