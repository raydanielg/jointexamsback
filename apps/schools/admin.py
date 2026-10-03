from django.contrib import admin

from .models import School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("school_name", "school_code", "location", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("school_name", "school_code", "registration_number")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("id", "school_name", "school_code", "registration_number", "status")}),
        ("Contact", {"fields": ("location", "phone", "email", "logo")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
