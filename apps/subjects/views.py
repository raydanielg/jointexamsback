from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission, accessible_school_ids

from .models import Subject
from .serializers import SubjectSerializer


class SubjectViewSet(viewsets.ModelViewSet):
    serializer_class = SubjectSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("status", "school")
    search_fields = ("name", "code", "short_name")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    write_roles = ("EXAM_ADMIN", "EXAM_ADMIN")

    def get_queryset(self):
        qs = Subject.objects.select_related("school")
        user = self.request.user
        if user.is_authenticated:
            return qs.filter(
                Q(school__in=accessible_school_ids(self.request))
                | Q(school__isnull=True)
            )
        return qs.none()

    def perform_update(self, serializer):
        instance = self.get_object()
        # Global subjects can only be modified by super admins.
        if instance.school_id is None and not self.request.user.is_superadmin:
            from apps.core.exceptions import BusinessRuleError

            raise BusinessRuleError("Global subjects are read-only.", code="PERMISSION_DENIED")
        serializer.save()

    def perform_destroy(self, instance):
        self.perform_update_check(instance)
        instance.delete()

    def perform_update_check(self, instance):
        if instance.school_id is None and not self.request.user.is_superadmin:
            from apps.core.exceptions import BusinessRuleError

            raise BusinessRuleError("Global subjects are read-only.", code="PERMISSION_DENIED")
