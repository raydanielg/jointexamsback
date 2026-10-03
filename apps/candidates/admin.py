from django.contrib import admin

from .models import Candidate


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_number", "first_name", "last_name", "gender", "school", "status"
    )
    list_filter = ("school", "status", "gender")
    search_fields = ("candidate_number", "first_name", "last_name")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("id", "school", "candidate_number", "status")}),
        ("Identity", {"fields": ("first_name", "middle_name", "last_name", "gender", "photo")}),
        ("Contacts", {"fields": ("phone", "guardian_name", "guardian_phone", "guardian_phone_2")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
