from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import IsSuperAdmin, RolePermission
from apps.core.mixins import SchoolScopedQuerySetMixin

from .models import SchoolSetting, SystemSetting
from .serializers import SchoolSettingSerializer, SystemSettingSerializer


class SchoolSettingViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = SchoolSettingSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = SchoolSetting.objects.all()
    filterset_fields = ("key",)
    write_roles = ("EXAM_ADMIN",)


class SystemSettingViewSet(viewsets.ModelViewSet):
    serializer_class = SystemSettingSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    queryset = SystemSetting.objects.all()
