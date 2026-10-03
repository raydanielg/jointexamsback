from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ImportSessionViewSet

router = DefaultRouter()
router.register("", ImportSessionViewSet, basename="import")

urlpatterns = [path("", include(router.urls))]
