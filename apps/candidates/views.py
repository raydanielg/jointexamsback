from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission
from apps.audit.services import log_action
from apps.core.mixins import SchoolScopedQuerySetMixin

from .filters import CandidateFilter
from .models import Candidate
from .serializers import CandidateSerializer


class CandidateViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = CandidateSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = Candidate.objects.select_related("school")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = CandidateFilter
    search_fields = ("first_name", "middle_name", "last_name", "candidate_number")
    ordering_fields = ("candidate_number", "last_name", "created_at")
    ordering = ("num_seq", "candidate_number")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    write_roles = ("EXAM_ADMIN", "SCHOOL_COORDINATOR")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            return qs
        # Numeric ordering — trailing digits of the candidate number so
        # JNT/2026/0001..0100 line up regardless of prefix.
        from apps.core.db import numeric_suffix

        return qs.annotate(num_seq=numeric_suffix())

    def perform_create(self, serializer):
        candidate = serializer.save()
        log_action(actor=self.request.user, action="CANDIDATE_CREATE", entity=candidate, request=self.request)

    def perform_update(self, serializer):
        candidate = serializer.save()
        log_action(actor=self.request.user, action="CANDIDATE_UPDATE", entity=candidate, request=self.request)

    def perform_destroy(self, instance):
        if instance.exam_enrollments.exists():
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                "Candidate has examination enrollments; set status to INACTIVE instead."
            )
        instance.delete()
