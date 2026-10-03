from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import (
    HasPermission,
    IsSuperAdmin,
    RolePermission,
    resolve_active_school,
)
from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError
from apps.core.responses import ok

from .models import School
from .serializers import SchoolSerializer


class SchoolViewSet(viewsets.ModelViewSet):
    serializer_class = SchoolSerializer
    filterset_fields = ("status",)
    search_fields = ("school_name", "school_code", "registration_number")
    ordering_fields = ("school_name", "created_at")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    write_roles = ("EXAM_ADMIN",)

    def get_permissions(self):
        # Reads need no school context — this endpoint is how clients pick one;
        # the queryset already scopes rows to the caller's memberships.
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated()]
        if self.action in ("destroy", "activate", "deactivate"):
            return [IsAuthenticated(), IsSuperAdmin()]
        if self.action == "create":
            # Super admins create top-level schools; org admins create centers
            # under their active organization.
            if getattr(self.request.user, "is_superadmin", False):
                return [IsAuthenticated()]
            return [IsAuthenticated(), HasPermission.with_permissions("centers.manage")]
        return [IsAuthenticated(), RolePermission()]

    def get_queryset(self):
        qs = School.objects.all()
        user = self.request.user
        if user.is_superadmin:
            return qs
        from django.db.models import Q

        # Members see their own schools, their child centers, plus schools
        # co-participating in examinations organized by their school.
        member_ids = user.memberships.filter(is_active=True).values_list("school_id", flat=True)
        shared = Q(organized_examinations__participating_schools__in=member_ids) | Q(
            examination_participation__school__in=member_ids
        )
        return qs.filter(
            Q(pk__in=member_ids) | Q(parent_id__in=member_ids) | shared
        ).distinct()

    def perform_update(self, serializer):
        instance = self.get_object()
        serializer.save()
        log_action(actor=self.request.user, action="SCHOOL_UPDATE", entity=instance, request=self.request)

    def perform_create(self, serializer):
        kwargs = {}
        if not self.request.user.is_superadmin:
            resolve_active_school(self.request)
            if getattr(self.request, "school", None) is None:
                raise BusinessRuleError(
                    "An active organization is required.", code="VALIDATION_ERROR"
                )
            kwargs["parent"] = self.request.school
        school = serializer.save(**kwargs)
        log_action(actor=self.request.user, action="SCHOOL_CREATE", entity=school, request=self.request)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        school = self.get_object()
        school.status = School.Status.ACTIVE
        school.save(update_fields=["status", "updated_at"])
        log_action(actor=request.user, action="SCHOOL_ACTIVATE", entity=school, request=request)
        return ok(SchoolSerializer(school, context={"request": request}).data, message="School activated.")

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        school = self.get_object()
        school.status = School.Status.INACTIVE
        school.save(update_fields=["status", "updated_at"])
        log_action(actor=request.user, action="SCHOOL_DEACTIVATE", entity=school, request=request)
        return ok(SchoolSerializer(school, context={"request": request}).data, message="School deactivated.")
