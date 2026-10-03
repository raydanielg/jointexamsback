from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ReportJobViewSet, ReportTemplateViewSet

router = DefaultRouter()
router.register("templates", ReportTemplateViewSet, basename="report-template")
router.register("", ReportJobViewSet, basename="report")

urlpatterns = [path("", include(router.urls))]
