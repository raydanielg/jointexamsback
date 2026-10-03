from django.contrib import admin

from .models import CandidateList, CandidateListEntry


class EntryInline(admin.TabularInline):
    model = CandidateListEntry
    extra = 0
    autocomplete_fields = ("candidate",)


@admin.register(CandidateList)
class CandidateListAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "cohort", "status", "candidate_count")
    list_filter = ("school", "status")
    search_fields = ("name", "cohort")
    inlines = [EntryInline]


@admin.register(CandidateListEntry)
class CandidateListEntryAdmin(admin.ModelAdmin):
    list_display = ("list", "candidate", "added_by", "created_at")
    list_filter = ("list__school",)
    autocomplete_fields = ("list", "candidate")
