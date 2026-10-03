from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AuthenticationAuditLog,
    EmailVerificationToken,
    PasswordResetToken,
    Permission,
    RolePermission,
    SchoolMembership,
    User,
    UserInvitation,
    UserRole,
    UserSession,
)


class MembershipInline(admin.TabularInline):
    model = SchoolMembership
    extra = 0
    autocomplete_fields = ("school",)


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "first_name", "last_name", "status", "is_superadmin", "is_staff")
    list_filter = ("status", "is_superadmin", "is_staff", "email_verified")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    inlines = [MembershipInline, UserRoleInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("first_name", "last_name", "phone")}),
        (
            "Permissions",
            {
                "fields": (
                    "status",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "is_superadmin",
                    "email_verified",
                    "must_change_password",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Timestamps", {"fields": ("last_login_at", "created_at", "updated_at")}),
    )
    readonly_fields = ("created_at", "updated_at", "last_login_at", "is_active")


@admin.register(SchoolMembership)
class SchoolMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "school", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("user__email", "school__school_name")
    autocomplete_fields = ("user", "school")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission")
    list_filter = ("role",)
    autocomplete_fields = ("permission",)


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    autocomplete_fields = ("user",)


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "device", "ip_address", "last_activity_at", "expires_at", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("user__email", "device", "ip_address")
    readonly_fields = ("refresh_jti", "user_agent", "ip_address", "device")


class SecureTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "expires_at", "used_at", "created_at")
    search_fields = ("user__email",)
    readonly_fields = ("token_hash", "expires_at", "used_at")
    exclude = ()


admin.site.register(PasswordResetToken, SecureTokenAdmin)
admin.site.register(EmailVerificationToken, SecureTokenAdmin)
admin.site.register(UserInvitation, SecureTokenAdmin)


@admin.register(AuthenticationAuditLog)
class AuthenticationAuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "email", "user", "ip_address", "created_at")
    list_filter = ("action",)
    search_fields = ("email", "user__email")
    readonly_fields = [f.name for f in AuthenticationAuditLog._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
