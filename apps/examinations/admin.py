from django.contrib import admin

from .models import ExamComponent, Examination, ExamSubject


class ExamComponentInline(admin.TabularInline):
    model = ExamComponent
    extra = 0


class ExamSubjectInline(admin.TabularInline):
    model = ExamSubject
    extra = 0
    autocomplete_fields = ("subject",)


@admin.register(Examination)
class ExaminationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "term", "status", "created_at")
    list_filter = ("status", "term", "school")
    search_fields = ("name", "code")
    filter_horizontal = ("participating_schools", "candidate_lists")
    inlines = [ExamSubjectInline]
    readonly_fields = ("id", "published_at", "finalized_at", "finalized_by", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("id", "name", "code", "school", "term", "period", "status")}),
        ("Schedule", {"fields": ("start_date", "end_date")}),
        ("Configuration", {"fields": (
            "description", "instructions", "grading_scheme", "ranking_config",
            "report_template", "sms_template", "division_best_subjects",
        )}),
        ("Publication", {"fields": ("published_at", "finalized_at", "finalized_by")}),
        ("Timestamps", {"fields": ("created_by", "created_at", "updated_at")}),
    )


@admin.register(ExamSubject)
class ExamSubjectAdmin(admin.ModelAdmin):
    list_display = ("subject", "examination", "maximum_marks", "pass_mark", "marks_status", "status")
    list_filter = ("examination__school", "marks_status", "status")
    search_fields = ("subject__name", "examination__name")
    autocomplete_fields = ("examination", "subject")
    inlines = [ExamComponentInline]


@admin.register(ExamComponent)
class ExamComponentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "exam_subject", "maximum_marks", "weight", "required")
    list_filter = ("status", "required")
    search_fields = ("name", "code")
