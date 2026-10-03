from rest_framework import mixins, viewsets

from apps.accounts.permissions import RolePermission

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only audit trail for the active school."""

    serializer_class = AuditLogSerializer
    permission_classes = [RolePermission]
    read_roles = ("EXAM_ADMIN", "REPORT_VIEWER")
    filterset_fields = ("action", "entity_type", "entity_id", "actor")
    search_fields = ("action", "entity_id")
    ordering_fields = ("created_at",)

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor", "school")
        user = self.request.user
        if getattr(self, "swagger_fake_view", False):
            return qs.none()
        if getattr(self.request, "school", None):
            return qs.filter(school=self.request.school)
        if user.is_superadmin:
            return qs
        return qs.none()
