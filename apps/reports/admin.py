from django.contrib import admin

from .models import ReportJob, ReportTemplate


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "report_type", "is_default")
    list_filter = ("school", "report_type")


@admin.register(ReportJob)
class ReportJobAdmin(admin.ModelAdmin):
    list_display = ("report_type", "examination", "format", "status", "requested_by", "created_at")
    list_filter = ("status", "report_type", "format")
    readonly_fields = ("error", "file")
