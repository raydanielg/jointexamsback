from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import SMSCampaignViewSet, SMSMessageViewSet, SMSTemplateViewSet

router = DefaultRouter()
router.register("templates", SMSTemplateViewSet, basename="sms-template")
router.register("campaigns", SMSCampaignViewSet, basename="sms-campaign")
router.register("messages", SMSMessageViewSet, basename="sms-message")

urlpatterns = [path("", include(router.urls))]
