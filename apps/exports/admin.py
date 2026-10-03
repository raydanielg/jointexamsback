from django.contrib import admin

from .models import ExportJob


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    list_display = ("export_type", "format", "school", "status", "requested_by", "created_at")
    list_filter = ("school", "export_type", "status")
