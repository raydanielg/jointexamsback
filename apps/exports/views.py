from django.http import HttpResponse
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import created

from .models import ExportJob
from .services import ExportService
from .tasks import run_export_task


class ExportJobSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ExportJob
        fields = ("id", "export_type", "format", "params", "status", "file", "file_url", "error", "completed_at", "created_at")
        read_only_fields = fields

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get("request")
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        return None


class CreateExportSerializer(serializers.Serializer):
    export_type = serializers.ChoiceField(choices=[c for c in ExportJob.ExportType.choices])
    format = serializers.ChoiceField(choices=(("CSV", "CSV"), ("XLSX", "XLSX")), default="CSV")
    params = serializers.DictField(required=False)
    run_async = serializers.BooleanField(default=True)


class ExportJobViewSet(SchoolScopedQuerySetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ExportJobSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = ExportJob.objects.select_related("school", "requested_by")
    filterset_fields = ("export_type", "status")

    write_roles = ("EXAM_ADMIN", "REPORT_VIEWER")

    @action(detail=False, methods=["post"], url_path="create")
    def create_export(self, request):
        serializer = CreateExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = ExportService.create_job(
            request.school,
            serializer.validated_data["export_type"],
            serializer.validated_data["format"],
            params=serializer.validated_data.get("params"),
            actor=request.user,
        )
        if serializer.validated_data["run_async"]:
            run_export_task.delay(str(job.pk))
        else:
            ExportService.generate(job)
        return created(ExportJobSerializer(job, context={"request": request}).data,
                       message="Export queued.")

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if job.status != ExportJob.Status.COMPLETED or not job.file:
            raise BusinessRuleError("Export is not ready.", code="NOT_FOUND")
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if job.format == "XLSX"
            else "text/csv"
        )
        response = HttpResponse(job.file.read(), content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{job.file.name.split("/")[-1]}"'
        return response
