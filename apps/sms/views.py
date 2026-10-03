from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission, can_manage_exam, accessible_school_ids
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import ok
from apps.examinations.models import Examination

from .models import SMSCampaign, SMSMessage, SMSTemplate
from .serializers import (
    SendSMSSerializer,
    SMSCampaignSerializer,
    SMSMessageSerializer,
    SMSTemplateSerializer,
)
from .services import SMSService


def _exam_for(request, exam_id):
    schools = accessible_school_ids(request)
    exam = Examination.objects.filter(
        Q(school__in=schools) | Q(participating_schools__in=schools), pk=exam_id
    ).first()
    if exam is None:
        raise BusinessRuleError("Examination not found.", code="NOT_FOUND")
    return exam


class SMSTemplateViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = SMSTemplateSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = SMSTemplate.objects.all()
    write_roles = ("EXAM_ADMIN",)

    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        template = self.get_object()
        sample = {
            "candidate_name": "John Peter",
            "candidate_number": "001",
            "position": 5,
            "school_position": 2,
            "exam_name": "Joint Examination 2026",
            "school_name": "Demo School",
            "average": "72.5",
            "grade": "B",
            "division": "I",
            "total": "435",
        }
        return ok({"rendered": template.render(sample), "length": len(template.render(sample))})


class SMSCampaignViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SMSCampaignSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("examination", "status")
    write_roles = ("EXAM_ADMIN",)

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return SMSCampaign.objects.none()
        return SMSCampaign.objects.filter(
            Q(examination__school=school) | Q(examination__participating_schools=school)
        ).distinct().select_related("examination", "template")

    @action(detail=False, methods=["post"], url_path="send")
    def send(self, request):
        serializer = SendSMSSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = _exam_for(request, serializer.validated_data["examination"])
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        campaign, created = SMSService.queue_campaign(
            exam,
            candidate_ids=serializer.validated_data.get("candidate_ids"),
            template_id=serializer.validated_data.get("template_id"),
            resend_failed=serializer.validated_data["resend_failed"],
            idempotency_key=serializer.validated_data.get("idempotency_key", ""),
            actor=request.user,
        )
        if created and serializer.validated_data["run_async"]:
            SMSService.dispatch(campaign.pk)
        elif created:
            SMSService.process_campaign(campaign.pk)
        return ok(
            SMSCampaignSerializer(campaign).data,
            message="SMS campaign queued." if created else "Duplicate send suppressed.",
        )

    @action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request):
        exam = _exam_for(request, request.data.get("examination"))
        stats = SMSService.preview_count(exam, request.data.get("candidate_ids"))
        return ok(stats)

    @action(detail=True, methods=["post"], url_path="resend-failed")
    def resend_failed(self, request, pk=None):
        campaign = self.get_object()
        if not can_manage_exam(request, campaign.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        campaign = SMSService.resend_failed(campaign.pk, actor=request.user)
        if campaign.messages.filter(status=SMSMessage.Status.QUEUED).exists():
            SMSService.dispatch(campaign.pk)
        return ok(SMSCampaignSerializer(campaign).data, message="Failed SMS re-queued.")

    @action(detail=True, methods=["get"], url_path="summary")
    def summary(self, request, pk=None):
        return ok(SMSService.summary(self.get_object()))


class SMSMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SMSMessageSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("campaign", "status")

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return SMSMessage.objects.none()
        return SMSMessage.objects.filter(
            Q(campaign__examination__school=school)
            | Q(campaign__examination__participating_schools=school)
        ).distinct().select_related("exam_candidate__candidate")
