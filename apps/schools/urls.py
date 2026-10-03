from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import SchoolViewSet

router = DefaultRouter()
router.register("", SchoolViewSet, basename="school")

urlpatterns = [path("", include(router.urls))]
