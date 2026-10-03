from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import MarkChangeLogViewSet, MarkCommentViewSet, MarkViewSet

router = DefaultRouter()
router.register("entries", MarkViewSet, basename="mark")
router.register("change-log", MarkChangeLogViewSet, basename="mark-change-log")
router.register("comments", MarkCommentViewSet, basename="mark-comment")

urlpatterns = [path("", include(router.urls))]
