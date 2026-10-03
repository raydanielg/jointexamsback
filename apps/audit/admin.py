from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "entity_type", "entity_id", "actor", "school", "created_at")
    list_filter = ("action", "entity_type", "school")
    search_fields = ("entity_id", "actor__email", "action")
    readonly_fields = (
        "actor",
        "school",
        "action",
        "entity_type",
        "entity_id",
        "metadata",
        "ip_address",
        "user_agent",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
