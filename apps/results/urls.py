from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CandidateResultViewSet,
    PublicResultsView,
    PredefinedCommentViewSet,
    ResultCorrectionRequestViewSet,
    ResultSnapshotViewSet,
    ResultsWorkflowViewSet,
    SubjectResultViewSet,
)

router = DefaultRouter()
router.register("workflow", ResultsWorkflowViewSet, basename="results-workflow")
router.register("examination", CandidateResultViewSet, basename="exam-result")
router.register("subjects", SubjectResultViewSet, basename="subject-result")
router.register("snapshots", ResultSnapshotViewSet, basename="result-snapshot")
router.register("corrections", ResultCorrectionRequestViewSet, basename="correction")
router.register("comments", PredefinedCommentViewSet, basename="predefined-comment")

urlpatterns = [
    path("public/<uuid:token>/", PublicResultsView.as_view({"get": "list"})),
    path("public/<uuid:token>/pdf/", PublicResultsView.as_view({"get": "pdf"})),
    path("", include(router.urls)),
]
