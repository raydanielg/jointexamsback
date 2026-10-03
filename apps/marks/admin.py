from django.contrib import admin

from .models import Mark, MarkChangeLog, MarkComment


@admin.register(Mark)
class MarkAdmin(admin.ModelAdmin):
    list_display = ("exam_candidate", "component", "value", "status", "entered_by", "updated_at")
    list_filter = ("status", "component__exam_subject__examination")
    search_fields = ("exam_candidate__candidate_number", "exam_candidate__candidate__last_name")
    autocomplete_fields = ("exam_candidate", "component")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(MarkChangeLog)
class MarkChangeLogAdmin(admin.ModelAdmin):
    list_display = ("exam_candidate", "component", "action", "old_value", "new_value", "changed_by", "created_at")
    list_filter = ("action",)
    search_fields = ("exam_candidate__candidate_number",)
    readonly_fields = [f.name for f in MarkChangeLog._meta.fields]


@admin.register(MarkComment)
class MarkCommentAdmin(admin.ModelAdmin):
    list_display = ("examination", "scope", "exam_candidate", "created_by", "created_at")
    list_filter = ("scope", "examination")
