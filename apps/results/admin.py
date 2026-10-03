from django.contrib import admin

from .models import (
    CandidateExamResult,
    CandidateSubjectResult,
    PredefinedComment,
    ResultCorrectionRequest,
    ResultSnapshot,
)


@admin.register(CandidateSubjectResult)
class CandidateSubjectResultAdmin(admin.ModelAdmin):
    list_display = ("exam_candidate", "exam_subject", "percentage", "grade", "position", "status")
    list_filter = ("status", "exam_subject__examination", "exam_candidate__school")
    search_fields = ("exam_candidate__candidate_number",)
    readonly_fields = ("id", "computed_at")


@admin.register(CandidateExamResult)
class CandidateExamResultAdmin(admin.ModelAdmin):
    list_display = (
        "exam_candidate", "examination", "average_percentage", "grade",
        "division", "position", "status",
    )
    list_filter = ("status", "examination", "exam_candidate__school")
    search_fields = ("exam_candidate__candidate_number",)
    readonly_fields = ("id", "computed_at")


@admin.register(ResultSnapshot)
class ResultSnapshotAdmin(admin.ModelAdmin):
    list_display = ("examination", "kind", "version", "created_by", "created_at")
    list_filter = ("kind", "examination")
    readonly_fields = ("payload", "grading_scheme_snapshot", "ranking_config_snapshot")


@admin.register(ResultCorrectionRequest)
class ResultCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("examination", "exam_candidate", "component", "status", "requested_by", "created_at")
    list_filter = ("status", "examination")


@admin.register(PredefinedComment)
class PredefinedCommentAdmin(admin.ModelAdmin):
    list_display = ("code", "text", "category", "school")
    list_filter = ("school", "category")
