from django.contrib import admin

from .models import ExaminationCandidate


@admin.register(ExaminationCandidate)
class ExaminationCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_number", "candidate", "school", "examination", "status", "created_at"
    )
    list_filter = ("examination", "school", "status")
    search_fields = ("candidate_number", "candidate__first_name", "candidate__last_name")
    autocomplete_fields = ("examination", "candidate", "source_list")
    readonly_fields = ("id", "created_at", "updated_at")
