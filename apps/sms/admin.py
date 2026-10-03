from django.contrib import admin

from .models import SMSCampaign, SMSMessage, SMSTemplate


@admin.register(SMSTemplate)
class SMSTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "examination", "is_default")
    list_filter = ("school",)


@admin.register(SMSCampaign)
class SMSCampaignAdmin(admin.ModelAdmin):
    list_display = ("examination", "status", "total", "sent", "failed", "skipped", "created_at")
    list_filter = ("status", "examination")
    readonly_fields = ("error",)


@admin.register(SMSMessage)
class SMSMessageAdmin(admin.ModelAdmin):
    list_display = ("phone", "campaign", "status", "attempts", "sent_at")
    list_filter = ("status", "campaign__examination")
    search_fields = ("phone", "exam_candidate__candidate_number")
    readonly_fields = ("message", "provider_message_id", "failure_reason")
