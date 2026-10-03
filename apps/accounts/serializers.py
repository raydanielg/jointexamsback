from django.conf import settings
from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import (
    AuthenticationAuditLog,
    Permission,
    Role,
    SchoolMembership,
    User,
    UserSession,
)


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    schools = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "status",
            "is_active",
            "is_superadmin",
            "email_verified",
            "must_change_password",
            "last_login_at",
            "roles",
            "permissions",
            "schools",
            "created_at",
        )
        read_only_fields = (
            "id", "status", "is_active", "is_superadmin", "email_verified",
            "last_login_at", "roles", "permissions", "created_at",
        )

    def get_schools(self, obj):
        return [
            {
                "school_id": str(m.school_id),
                "school_name": m.school.school_name,
                "role": m.role,
            }
            for m in obj.memberships.filter(is_active=True).select_related("school")
        ]

    def get_roles(self, obj):
        return obj.effective_roles()

    def get_permissions(self, obj):
        return sorted(obj.permission_codes())


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Self-service profile fields only — roles/status/verification are
    managed by administrators."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "phone")


class RegisterSerializer(serializers.Serializer):
    """Self-service registration. Creates a user with no school membership —
    an administrator assigns school roles afterwards. When email verification
    is required, the account starts PENDING_VERIFICATION."""

    full_name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value):
        from .services import validate_password_strength

        validate_password_strength(value)
        return value

    def create(self, validated_data):
        import uuid

        from django.db import transaction

        from apps.schools.models import School

        parts = validated_data["full_name"].strip().split(None, 1)
        with transaction.atomic():
            user = User.objects.create_user(
                email=validated_data["email"],
                password=validated_data["password"],
                phone=validated_data["phone"],
                first_name=parts[0],
                last_name=parts[1] if len(parts) > 1 else "",
                status=(
                    User.Status.PENDING_VERIFICATION
                    if settings.EMAIL_VERIFICATION_REQUIRED
                    else User.Status.ACTIVE
                ),
            )
            # Every registrant becomes the administrator of their own
            # examination organization — full control of their exams,
            # candidates, marks, results and users within that scope.
            first = parts[0] or "My"
            school = School.objects.create(
                school_name=f"{first}'s Organization",
                school_code=f"ORG-{uuid.uuid4().hex[:6].upper()}",
            )
            SchoolMembership.objects.create(
                user=user, school=school, role=Role.EXAM_ADMIN
            )
            from apps.grading.services import GradeCalculationService

            GradeCalculationService.ensure_default_scheme(school)
        return user


class ClaimOrganizationSerializer(serializers.Serializer):
    """Creates an examination organization for a user who has none — the
    user becomes its EXAM_ADMIN (organizer)."""

    organization_name = serializers.CharField(max_length=200)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        request = self.context.get("request")
        user = authenticate(
            request, username=attrs["email"].lower(), password=attrs["password"]
        )
        if user is None:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("Invalid email or password.")
        if settings.EMAIL_VERIFICATION_REQUIRED and not user.email_verified:
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )
        from .services import validate_password_strength

        validate_password_strength(
            attrs["new_password"], user=self.context["request"].user
        )
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )
        return attrs


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )
        return attrs


class UserSessionSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = (
            "id",
            "device",
            "ip_address",
            "user_agent",
            "is_active",
            "is_current",
            "last_activity_at",
            "expires_at",
            "created_at",
        )

    def get_is_current(self, obj):
        request = self.context.get("request")
        auth = getattr(request, "auth", None)
        sid = auth.get("sid") if auth else None
        return bool(sid and str(obj.pk) == str(sid))


class AdminUserCreateSerializer(serializers.Serializer):
    """Administrator user creation — an invitation email is sent; no password
    is transmitted or set by the admin."""

    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=Role.choices, required=False, allow_null=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_role(self, value):
        if value == Role.SUPER_ADMIN:
            raise serializers.ValidationError(
                "SUPER_ADMIN cannot be assigned as a role."
            )
        return value


class AssignRolesSerializer(serializers.Serializer):
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=Role.choices), allow_empty=True
    )

    def validate_roles(self, value):
        if Role.SUPER_ADMIN in value:
            raise serializers.ValidationError(
                "SUPER_ADMIN cannot be assigned as a role."
            )
        return value


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "phone", "email_verified")


class RoleDetailSerializer(serializers.Serializer):
    role = serializers.CharField()
    label = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ("code", "name", "description")


class MembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    school_name = serializers.CharField(source="school.school_name", read_only=True)

    class Meta:
        model = SchoolMembership
        fields = (
            "id",
            "user",
            "user_email",
            "user_name",
            "school",
            "school_name",
            "role",
            "is_active",
            "created_at",
        )

    def validate_role(self, value):
        if value == Role.SUPER_ADMIN:
            raise serializers.ValidationError(
                "SUPER_ADMIN is granted via the user record, not memberships."
            )
        return value


class AuthAuditLogSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = AuthenticationAuditLog
        fields = (
            "id", "user", "user_email", "email", "action",
            "ip_address", "user_agent", "metadata", "created_at",
        )


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class EmptySerializer(serializers.Serializer):
    pass
