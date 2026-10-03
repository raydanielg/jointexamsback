from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import UUIDModel


class Role(models.TextChoices):
    """EMAS roles. SUPER_ADMIN is a user flag (is_superadmin), not a
    membership role. Membership roles are school-scoped."""

    SUPER_ADMIN = "SUPER_ADMIN", "Super Administrator"
    EXAM_ADMIN = "EXAM_ADMIN", "Examination Administrator"
    SCHOOL_COORDINATOR = "SCHOOL_COORDINATOR", "School Coordinator"
    MARKS_ENTRY = "MARKS_ENTRY", "Marks Entry"
    REPORT_VIEWER = "REPORT_VIEWER", "Report Viewer"


MEMBER_ROLES = (
    Role.EXAM_ADMIN,
    Role.SCHOOL_COORDINATOR,
    Role.MARKS_ENTRY,
    Role.REPORT_VIEWER,
)


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_superadmin", True)
        extra.setdefault("is_active", True)
        extra.setdefault("email_verified", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, UUIDModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        SUSPENDED = "SUSPENDED", "Suspended"
        PENDING_VERIFICATION = "PENDING_VERIFICATION", "Pending verification"

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superadmin = models.BooleanField(
        default=False,
        help_text="Cross-school administrator. Can access any school's data.",
    )
    email_verified = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    last_login_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        ordering = ("last_name", "first_name")

    def __str__(self):
        return f"{self.get_full_name()} <{self.email}>"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def schools(self):
        from apps.schools.models import School

        return School.objects.filter(memberships__user=self, memberships__is_active=True)

    def role_for(self, school):
        if self.is_superadmin:
            return Role.SUPER_ADMIN
        membership = self.memberships.filter(school=school, is_active=True).first()
        return membership.role if membership else None

    def has_school_access(self, school):
        if self.is_superadmin:
            return True
        return self.memberships.filter(school=school, is_active=True).exists()

    def record_login(self):
        self.last_login_at = timezone.now()
        self.save(update_fields=["last_login_at"])

    def save(self, *args, **kwargs):
        # is_active mirrors the account status so Django's auth backend and
        # session checks stay consistent automatically.
        self.is_active = self.status == self.Status.ACTIVE
        super().save(*args, **kwargs)

    # -- roles / permissions ------------------------------------------------

    def effective_roles(self, school=None):
        """All roles the user holds: direct role assignments plus active
        school-membership roles. ``school`` restricts membership roles to
        a single school when supplied."""
        roles = set(self.user_roles.values_list("role", flat=True))
        memberships = self.memberships.filter(is_active=True)
        if school is not None:
            memberships = memberships.filter(school=school)
        roles.update(memberships.values_list("role", flat=True))
        if self.is_superadmin:
            roles.add(Role.SUPER_ADMIN)
        return sorted(roles)

    def permission_codes(self):
        """Effective permission codes derived from all assigned roles."""
        if self.is_superadmin:
            return set(Permission.objects.values_list("code", flat=True))
        roles = set(self.user_roles.values_list("role", flat=True)) | set(
            self.memberships.filter(is_active=True).values_list("role", flat=True)
        )
        if not roles:
            return set()
        return set(
            Permission.objects.filter(role_permissions__role__in=roles)
            .values_list("code", flat=True)
        )

    def has_permission(self, code):
        return code in self.permission_codes()


class Permission(UUIDModel):
    """Data-driven permission code, e.g. ``exams.view``."""

    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("code",)

    def __str__(self):
        return self.code


class RolePermission(UUIDModel):
    """Grants a permission to a role."""

    role = models.CharField(max_length=30, choices=Role.choices)
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("role", "permission"), name="unique_role_permission")
        ]

    def __str__(self):
        return f"{self.role} → {self.permission_id}"


class UserRole(UUIDModel):
    """Extra (non-school-scoped) role assigned directly to a user."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.CharField(max_length=30, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "role"), name="unique_user_role")
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.role == Role.SUPER_ADMIN:
            raise ValidationError("SUPER_ADMIN is granted via User.is_superadmin.")

    def __str__(self):
        return f"{self.user} ({self.role})"


class SchoolMembership(UUIDModel):
    """Links a user to a school with a role. Supports users belonging to
    multiple schools through separate memberships."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=30, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "school"), name="unique_user_school_membership")
        ]
        indexes = [models.Index(fields=("school", "role"))]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.role == Role.SUPER_ADMIN:
            raise ValidationError(
                "SUPER_ADMIN is granted via User.is_superadmin, not memberships."
            )

    def __str__(self):
        return f"{self.user} @ {self.school} ({self.role})"


class UserSession(UUIDModel):
    """Tracks an authenticated session/device. Stores the refresh-token JTI
    (an identifier, never the raw token) so sessions can be revoked."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    refresh_jti = models.CharField(max_length=64, unique=True)
    device = models.CharField(max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    last_activity_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=("user", "revoked_at")),
            models.Index(fields=("refresh_jti",)),
        ]

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])

    def __str__(self):
        return f"{self.user} @ {self.device or 'unknown device'}"


class SecureToken(UUIDModel):
    """Abstract base for single-use, expiring secrets. Only the SHA-256 hash
    of the token is stored — never the raw token."""

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @classmethod
    def issue(cls, user, ttl, **extra):
        import hashlib
        import secrets

        raw = secrets.token_urlsafe(32)
        instance = cls.objects.create(
            user=user,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=timezone.now() + ttl,
            **extra,
        )
        return instance, raw

    @classmethod
    def find(cls, raw):
        import hashlib

        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        instance = cls.objects.filter(token_hash=token_hash).first()
        if instance is None or instance.used_at or instance.expires_at <= timezone.now():
            return None
        return instance

    def consume(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])


class PasswordResetToken(SecureToken):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_reset_tokens"
    )


class EmailVerificationToken(SecureToken):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="email_verification_tokens"
    )


class UserInvitation(SecureToken):
    """Invitation created by an administrator for a not-yet-active user."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invitations")
    invited_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    school = models.ForeignKey(
        "schools.School", on_delete=models.SET_NULL, null=True, blank=True
    )
    role = models.CharField(max_length=30, choices=Role.choices, null=True, blank=True)


class AuthenticationAuditLog(models.Model):
    """Append-only authentication event log. Never stores credentials."""

    class Action(models.TextChoices):
        LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login success"
        LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
        LOGIN_BLOCKED = "LOGIN_BLOCKED", "Login temporarily blocked"
        LOGOUT = "LOGOUT", "Logout"
        LOGOUT_ALL = "LOGOUT_ALL", "Logout all devices"
        PASSWORD_CHANGE = "PASSWORD_CHANGE", "Password changed"
        PASSWORD_RESET_REQUEST = "PASSWORD_RESET_REQUEST", "Password reset requested"
        PASSWORD_RESET = "PASSWORD_RESET", "Password reset completed"
        EMAIL_VERIFICATION_SENT = "EMAIL_VERIFICATION_SENT", "Verification email sent"
        EMAIL_VERIFIED = "EMAIL_VERIFIED", "Email verified"
        INVITATION_SENT = "INVITATION_SENT", "Invitation sent"
        INVITATION_ACCEPTED = "INVITATION_ACCEPTED", "Invitation accepted"
        USER_CREATED = "USER_CREATED", "User created"
        ACCOUNT_ACTIVATED = "ACCOUNT_ACTIVATED", "Account activated"
        ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED", "Account suspended"
        ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED", "Account deactivated"
        ROLES_CHANGED = "ROLES_CHANGED", "Roles changed"
        SESSION_REVOKED = "SESSION_REVOKED", "Session revoked"
        REGISTERED = "REGISTERED", "Self-registration"

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="auth_events"
    )
    email = models.EmailField(blank=True, help_text="Attempted email for anonymous events.")
    action = models.CharField(max_length=40, choices=Action.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "created_at")),
            models.Index(fields=("action", "created_at")),
        ]

    def __str__(self):
        return f"{self.action} {self.user_id or self.email}"
