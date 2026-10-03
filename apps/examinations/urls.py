from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExamComponentViewSet, ExaminationViewSet, ExamSubjectViewSet

router = DefaultRouter()
router.register("examinations", ExaminationViewSet, basename="examination")
router.register("exam-subjects", ExamSubjectViewSet, basename="exam-subject")
router.register("exam-components", ExamComponentViewSet, basename="exam-component")

urlpatterns = [path("", include(router.urls))]
