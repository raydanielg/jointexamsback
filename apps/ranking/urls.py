from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import RankingConfigurationViewSet, RankingViewSet

router = DefaultRouter()
router.register("configurations", RankingConfigurationViewSet, basename="ranking-config")
router.register("", RankingViewSet, basename="ranking")

urlpatterns = [path("", include(router.urls))]
