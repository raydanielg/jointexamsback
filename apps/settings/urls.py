from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import SchoolSettingViewSet, SystemSettingViewSet

router = DefaultRouter()
router.register("school", SchoolSettingViewSet, basename="school-setting")
router.register("system", SystemSettingViewSet, basename="system-setting")

urlpatterns = [path("", include(router.urls))]
