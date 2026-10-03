from django.contrib import admin

from .models import ImportSession


@admin.register(ImportSession)
class ImportSessionAdmin(admin.ModelAdmin):
    list_display = ("import_type", "school", "status", "total_rows", "success_rows", "failed_rows", "created_by")
    list_filter = ("school", "import_type", "status")
    readonly_fields = ("errors", "error_report", "completed_at")
