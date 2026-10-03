from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExaminationCandidateViewSet

router = DefaultRouter()
router.register("", ExaminationCandidateViewSet, basename="exam-candidate")

urlpatterns = [path("", include(router.urls))]
