import logging

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError
from apps.core.responses import created, ok

from .models import (
    AuthenticationAuditLog as Audit,
    Permission,
    Role,
    SchoolMembership,
    User,
    UserRole,
    UserSession,
)
from .permissions import HasPermission, IsSuperAdmin, RolePermission, resolve_active_school
from . import serializers as sz
from . import services

logger = logging.getLogger("emas.accounts")


def _tokens_for(user, request=None):
    """Issue tokens and create a UserSession bound to the refresh JTI."""
    refresh = RefreshToken.for_user(user)
    session = None
    if request is not None:
        session = services.create_session(user, request, refresh)
        refresh["sid"] = str(session.id)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "session_id": str(session.id) if session else None,
    }


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = sz.LoginSerializer

    @extend_schema(request=sz.LoginSerializer, responses={200: None})
    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        ip = services.client_ip(request)

        if services.is_login_blocked(email, ip):
            services.log_auth_event(Audit.Action.LOGIN_BLOCKED, request=request, email=email)
            raise BusinessRuleError(
                "Too many login attempts. Try again later.", code="RATE_LIMITED"
            )

        serializer = sz.LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            blocked = services.record_login_failure(email, ip)
            services.log_auth_event(Audit.Action.LOGIN_FAILED, request=request, email=email)
            raise BusinessRuleError(
                "Invalid email or password.", code="INVALID_CREDENTIALS"
            )

        user = serializer.validated_data["user"]
        services.clear_login_failures(email, ip)
        user.record_login()
        services.log_auth_event(Audit.Action.LOGIN_SUCCESS, request=request, user=user)
        log_action(actor=user, action="LOGIN", entity=user, metadata={"email": user.email}, request=request)

        data = {**_tokens_for(user, request), "user": sz.UserSerializer(user).data}
        return ok(data, message="Login successful.")


class RefreshView(TokenRefreshView):
    """Token refresh that keeps the session's refresh-JTI current."""

    def post(self, request, *args, **kwargs):
        old_jti = None
        raw = request.data.get("refresh")
        if raw:
            try:
                old_jti = RefreshToken(raw)["jti"]
            except TokenError:
                pass
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200 and old_jti:
            try:
                session = UserSession.objects.get(refresh_jti=old_jti, revoked_at=None)
                new_refresh = RefreshToken(response.data["refresh"])
                session.refresh_jti = new_refresh["jti"]
                session.save(update_fields=["refresh_jti", "updated_at"])
            except Exception:  # noqa: BLE001
                pass
        return response


class LogoutView(APIView):
    serializer_class = sz.LogoutSerializer

    def post(self, request):
        serializer = sz.LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refresh = RefreshToken(serializer.validated_data["refresh"])
            refresh.blacklist()
        except TokenError:
            raise BusinessRuleError("Invalid refresh token.", code="TOKEN_INVALID")
        session = UserSession.objects.filter(
            user=request.user, refresh_jti=refresh["jti"], revoked_at=None
        ).first()
        if session:
            session.revoke()
        services.log_auth_event(Audit.Action.LOGOUT, request=request, user=request.user)
        log_action(actor=request.user, action="LOGOUT", entity=request.user, request=request)
        return ok(message="Logout successful.")


class LogoutAllView(APIView):
    serializer_class = sz.EmptySerializer

    def post(self, request):
        sid = request.auth.get("sid") if request.auth else None
        current = UserSession.objects.filter(pk=sid).first() if sid else None
        services.revoke_all_sessions(request.user, exclude=current)
        if current:
            current.revoke()
            services.blacklist_jti(current.refresh_jti)
        services.log_auth_event(Audit.Action.LOGOUT_ALL, request=request, user=request.user)
        return ok(message="Logged out of all sessions.")


class MeView(generics.RetrieveUpdateAPIView):
    """GET returns the full authenticated profile; PATCH updates basic
    profile fields only."""

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return sz.ProfileUpdateSerializer
        return sz.UserSerializer


class ProfileView(generics.UpdateAPIView):
    serializer_class = sz.ProfileUpdateSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    serializer_class = sz.ChangePasswordSerializer

    def post(self, request):
        serializer = sz.ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        services.set_password(user, serializer.validated_data["new_password"])
        # Revoke every other session; the current session stays valid.
        sid = request.auth.get("sid") if request.auth else None
        current = UserSession.objects.filter(pk=sid).first() if sid else None
        services.revoke_all_sessions(user, exclude=current)
        services.log_auth_event(Audit.Action.PASSWORD_CHANGE, request=request, user=user)
        log_action(actor=user, action="PASSWORD_CHANGE", entity=user, request=request)
        return ok(message="Password changed.")


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = sz.ForgotPasswordSerializer

    def post(self, request):
        serializer = sz.ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        user = User.objects.filter(email__iexact=email).first()
        if user and user.is_active:
            services.request_password_reset(user)
            services.log_auth_event(
                Audit.Action.PASSWORD_RESET_REQUEST, request=request, user=user
            )
        return ok(
            message="If an account exists for this email, password reset instructions have been sent."
        )


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = sz.ResetPasswordSerializer

    def post(self, request):
        serializer = sz.ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.reset_password(
            serializer.validated_data["token"], serializer.validated_data["new_password"]
        )
        services.log_auth_event(Audit.Action.PASSWORD_RESET, request=request, user=user)
        return ok(message="Password has been reset.")


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    serializer_class = sz.VerifyEmailSerializer

    def post(self, request):
        serializer = sz.VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.verify_email(serializer.validated_data["token"])
        services.log_auth_event(Audit.Action.EMAIL_VERIFIED, request=request, user=user)
        return ok(message="Email verified.")


class ResendVerificationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = sz.ResendVerificationSerializer

    def post(self, request):
        serializer = sz.ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"]
        ).first()
        if user and not user.email_verified:
            services.send_verification_email(user)
            services.log_auth_event(
                Audit.Action.EMAIL_VERIFICATION_SENT, request=request, user=user
            )
        return ok(message="If the email needs verification, a link has been sent.")


class AcceptInvitationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = sz.AcceptInvitationSerializer

    def post(self, request):
        serializer = sz.AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.accept_invitation(
            serializer.validated_data["token"], serializer.validated_data["new_password"]
        )
        services.log_auth_event(Audit.Action.INVITATION_ACCEPTED, request=request, user=user)
        data = {**_tokens_for(user, request), "user": sz.UserSerializer(user).data}
        return ok(data, message="Account activated.")


class RegisterView(APIView):
    """Public self-registration. The new account has no school membership,
    so it cannot see any school data until an administrator assigns one."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = sz.RegisterSerializer

    def post(self, request):
        serializer = sz.RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        services.log_auth_event(Audit.Action.REGISTERED, request=request, user=user)
        log_action(actor=user, action="USER_REGISTER", entity=user, metadata={"email": user.email}, request=request)
        data = {**_tokens_for(user, request), "user": sz.UserSerializer(user).data}
        return created(data, message="Account created.")


class ClaimOrganizationView(APIView):
    """Users without an organization create their own — becoming its
    EXAM_ADMIN with full exam-management permissions in that scope."""

    serializer_class = sz.ClaimOrganizationSerializer

    def post(self, request):
        serializer = sz.ClaimOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        school = services.claim_organization(
            request.user, serializer.validated_data["organization_name"]
        )
        services.log_auth_event(
            Audit.Action.USER_CREATED, request=request, user=request.user,
            metadata={"organization": school.school_name},
        )
        return created(
            sz.UserSerializer(request.user).data,
            message="Organization created.",
        )


class CreateOrganizationView(APIView):
    """Add another organization to the user's account. Users may administer
    multiple organizations and switch between them."""

    serializer_class = sz.ClaimOrganizationSerializer

    def post(self, request):
        serializer = sz.ClaimOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        school = services.create_owned_organization(
            request.user, serializer.validated_data["organization_name"]
        )
        services.log_auth_event(
            Audit.Action.USER_CREATED, request=request, user=request.user,
            metadata={"organization": school.school_name},
        )
        return created(
            {
                "school": {
                    "id": str(school.pk),
                    "school_name": school.school_name,
                    "school_code": school.school_code,
                },
                "user": sz.UserSerializer(request.user).data,
            },
            message="Organization created.",
        )


class SessionViewSet(viewsets.GenericViewSet):
    serializer_class = sz.UserSessionSerializer

    def get_queryset(self):
        return self.request.user.sessions.order_by("-last_activity_at")

    def list(self, request):
        return ok(self.get_serializer(self.get_queryset(), many=True).data)

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        session = self.get_object()
        if not session.is_active:
            raise BusinessRuleError("Session is not active.", code="SESSION_NOT_FOUND")
        services.revoke_session(session)
        services.log_auth_event(
            Audit.Action.SESSION_REVOKED, request=request, user=request.user,
            metadata={"session": str(session.pk)},
        )
        return ok(message="Session revoked.")


class RoleView(APIView):
    """List roles and their effective permission codes."""

    serializer_class = sz.RoleDetailSerializer

    def get(self, request):
        data = []
        for value, label in Role.choices:
            if value == Role.SUPER_ADMIN:
                perms = list(Permission.objects.values_list("code", flat=True))
            else:
                perms = list(
                    Permission.objects.filter(role_permissions__role=value)
                    .values_list("code", flat=True)
                )
            data.append({"role": value, "label": label, "permissions": sorted(perms)})
        return ok(data)


class AuthAuditViewSet(viewsets.GenericViewSet):
    """Read-only authentication history (admin only)."""

    serializer_class = sz.AuthAuditLogSerializer
    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ("audit.view",)

    def get_queryset(self):
        qs = Audit.objects.select_related("user")
        if self.request.query_params.get("user"):
            qs = qs.filter(user_id=self.request.query_params["user"])
        if self.request.query_params.get("action"):
            qs = qs.filter(action=self.request.query_params["action"])
        return qs

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(
            self.get_serializer(page, many=True).data
        )


class UserViewSet(viewsets.ModelViewSet):
    """User administration. Super admins manage all users; exam admins may
    list users of their active school and create users joined to it."""

    serializer_class = sz.UserSerializer
    queryset = User.objects.all().prefetch_related("memberships__school", "user_roles")
    filterset_fields = ("is_active", "email_verified", "status")
    search_fields = ("email", "first_name", "last_name")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ("users.view",)
    required_permissions_write = ("users.manage",)

    def get_queryset(self):
        qs = super().get_queryset()
        resolve_active_school(self.request)
        if getattr(self.request.user, "is_superadmin", False):
            if getattr(self.request, "school", None):
                return qs.filter(memberships__school=self.request.school)
            return qs
        if getattr(self.request, "school", None):
            return qs.filter(memberships__school=self.request.school)
        return qs.filter(pk=self.request.user.pk)

    def create(self, request, *args, **kwargs):
        serializer = sz.AdminUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _, school = resolve_active_school(request)
        user, _invitation = services.create_invitation(
            email=serializer.validated_data["email"],
            first_name=serializer.validated_data["first_name"],
            last_name=serializer.validated_data["last_name"],
            phone=serializer.validated_data["phone"],
            role=serializer.validated_data.get("role"),
            school=school,
            actor=request.user,
        )
        services.log_auth_event(
            Audit.Action.INVITATION_SENT, request=request, user=request.user,
            metadata={"invited": str(user.pk)},
        )
        log_action(actor=request.user, action="USER_CREATE", entity=user, request=request)
        return created(sz.UserSerializer(user).data, message="Invitation sent.")

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = sz.AdminUserUpdateSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return ok(sz.UserSerializer(instance).data, message="User updated.")

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        services.set_account_status(self.get_object(), User.Status.ACTIVE, actor=request.user, request=request)
        return ok(message="User activated.")

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        services.set_account_status(self.get_object(), User.Status.SUSPENDED, actor=request.user, request=request)
        return ok(message="User suspended.")

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        services.set_account_status(self.get_object(), User.Status.INACTIVE, actor=request.user, request=request)
        return ok(message="User deactivated.")

    @action(detail=True, methods=["post"], url_path="roles")
    def assign_roles(self, request, pk=None):
        user = self.get_object()
        serializer = sz.AssignRolesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        roles = serializer.validated_data["roles"]
        user.user_roles.all().delete()
        UserRole.objects.bulk_create([UserRole(user=user, role=r) for r in roles])
        services.log_auth_event(
            Audit.Action.ROLES_CHANGED, request=request, user=request.user,
            metadata={"target": str(user.pk), "roles": roles},
        )
        return ok(sz.UserSerializer(user).data, message="Roles updated.")


class MembershipViewSet(viewsets.ModelViewSet):
    serializer_class = sz.MembershipSerializer
    queryset = SchoolMembership.objects.select_related("user", "school")
    permission_classes = [IsAuthenticated]
    filterset_fields = ("role", "is_active", "school")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        resolve_active_school(self.request)
        if getattr(user, "is_superadmin", False):
            if getattr(self.request, "school", None):
                return qs.filter(school=self.request.school)
            return qs
        if getattr(self.request, "school", None):
            return qs.filter(school=self.request.school)
        return qs.filter(user=user)

    def perform_create(self, serializer):
        resolve_active_school(self.request)
        user = self.request.user
        school = serializer.validated_data["school"]
        if not user.is_superadmin:
            if getattr(self.request, "school", None) != school:
                raise BusinessRuleError(
                    "You can only create memberships for your active school.",
                    code="PERMISSION_DENIED",
                )
            if self.request.membership.role not in ("EXAM_ADMIN", "SCHOOL_COORDINATOR"):
                raise BusinessRuleError(
                    "Only examination administrators may assign memberships.",
                    code="PERMISSION_DENIED",
                )
        membership = serializer.save()
        log_action(
            actor=user,
            action="MEMBERSHIP_CREATE",
            entity=membership,
            request=self.request,
            metadata={"role": membership.role},
        )
