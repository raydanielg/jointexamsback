from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DivisionBandViewSet, GradeBandViewSet, GradingSchemeViewSet

router = DefaultRouter()
router.register("schemes", GradingSchemeViewSet, basename="grading-scheme")
router.register("bands", GradeBandViewSet, basename="grade-band")
router.register("division-bands", DivisionBandViewSet, basename="division-band")

urlpatterns = [path("", include(router.urls))]
