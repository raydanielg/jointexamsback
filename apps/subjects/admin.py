from django.contrib import admin

from .models import Subject


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "short_name", "school", "status")
    list_filter = ("school", "status")
    search_fields = ("name", "code")
