from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExportJobViewSet

router = DefaultRouter()
router.register("", ExportJobViewSet, basename="export")

urlpatterns = [path("", include(router.urls))]
