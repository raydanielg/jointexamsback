from django.http import HttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.permissions import RolePermission, write_school
from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import created, ok
from apps.core.validators import validate_spreadsheet_file

from .models import ImportSession
from .parsers import build_template, parse_spreadsheet
from .serializers import ImportSessionSerializer, ImportUploadSerializer
from .services import ImportService
from .tasks import process_import_task


class ImportSessionViewSet(SchoolScopedQuerySetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ImportSessionSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = ImportSession.objects.select_related("school", "created_by")
    filterset_fields = ("import_type", "status")
    write_roles = ("EXAM_ADMIN", "MARKS_ENTRY")
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "import"

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        serializer = ImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file_obj = serializer.validated_data["file"]
        validate_spreadsheet_file(file_obj)
        params = {
            k: str(serializer.validated_data[k])
            for k in ("candidate_list", "examination", "center")
            if serializer.validated_data.get(k)
        }
        edited = request.data.get("edited_rows")
        if edited:
            import json as _json

            try:
                params["edited_rows"] = _json.loads(edited)
            except (TypeError, ValueError):
                raise BusinessRuleError("Invalid edited_rows payload.", code="VALIDATION_ERROR")
        session = ImportSession.objects.create(
            school=write_school(request),
            import_type=serializer.validated_data["import_type"],
            file=file_obj,
            params=params,
            strict=serializer.validated_data["strict"],
            created_by=request.user,
        )
        log_action(actor=request.user, action="IMPORT_UPLOADED", entity=session, request=request)
        if serializer.validated_data["run_async"]:
            process_import_task.delay(str(session.pk))
        else:
            session = ImportService.process(session)
        return created(
            ImportSessionSerializer(session, context={"request": request}).data,
            message="Import started.",
        )

    @action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request):
        """Dry-run: parse a candidate sheet and return per-row validation so the
        UI can show a preview/mapping table before anything is created."""
        file_obj = request.FILES.get("file")
        if file_obj is None:
            raise BusinessRuleError("A file is required.", code="VALIDATION_ERROR")
        validate_spreadsheet_file(file_obj)
        from apps.schools.models import School

        from apps.accounts.permissions import write_school

        school = write_school(request)
        rows = parse_spreadsheet(file_obj, file_obj.name)
        if not rows:
            raise BusinessRuleError(
                "The file has no rows.", code="VALIDATION_ERROR"
            )
        preview = ImportService.preview_candidates(rows, school)
        return ok({
            "rows": preview,
            "total": len(preview),
            "valid": sum(1 for r in preview if r["status"] == "ok"),
            "errors": sum(1 for r in preview if r["status"] == "error"),
        }, message="Preview ready.")

    @action(detail=False, methods=["get"], url_path="template")
    def template(self, request):
        """Download an XLSX import template."""
        import_type = request.query_params.get("type", "STUDENTS")
        if import_type == "MARKS":
            columns = ["candidate_number", "subject_code", "component_code", "marks", "status"]
            example = ["001", "MATH", "PAPER1", "75", ""]
        else:
            # Minimal sheet — candidate numbers are assigned automatically.
            columns = ["full_name", "phone"]
            example = ["Asha Mushi", "+255700000000"]
        payload = build_template(columns, example)
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{import_type.lower()}_import_template.xlsx"'
        return response
