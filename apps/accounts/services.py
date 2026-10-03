"""Authentication services: secure tokens, sessions, brute-force protection,
password policy, invitations and the authentication audit log.

Never log or store raw secrets — only SHA-256 hashes of issued tokens.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import BusinessRuleError
from apps.grading.services import GradeCalculationService

from .models import (
    AuthenticationAuditLog,
    EmailVerificationToken,
    PasswordResetToken,
    SchoolMembership,
    User,
    UserInvitation,
    UserSession,
)

logger = logging.getLogger("emas.accounts")


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")[:500]


def _device_label(user_agent):
    ua = user_agent.lower()
    if not ua:
        return "Unknown device"
    if "iphone" in ua or "android" in ua and "mobile" in ua:
        os_name = "iPhone" if "iphone" in ua else "Android"
    elif "ipad" in ua or "android" in ua:
        os_name = "iPad" if "ipad" in ua else "Android"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "windows" in ua:
        os_name = "Windows"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown device"
    for name in ("edg", "chrome", "firefox", "safari", "opera"):
        if name in ua:
            browser = "Edge" if name == "edg" else name.capitalize()
            return f"{browser} / {os_name}"
    return os_name


def log_auth_event(action, request=None, user=None, email="", metadata=None):
    """Append-only auth event; failures never break the request."""
    try:
        AuthenticationAuditLog.objects.create(
            user=user,
            email=email or (user.email if user else ""),
            action=action,
            ip_address=client_ip(request) if request else None,
            user_agent=_user_agent(request) if request else "",
            metadata=metadata or {},
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write auth audit event %s", action)


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "qwerty123",
    "letmein123", "admin12345", "changeme", "11111111", "iloveyou",
}


def validate_password_strength(password, user=None):
    """Configurable password policy: length + optional complexity rules +
    Django's built-in validators + a small denylist."""
    errors = []
    if len(password or "") < settings.PASSWORD_MIN_LENGTH:
        errors.append(
            f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters."
        )
    if settings.PASSWORD_REQUIRE_UPPERCASE and not any(c.isupper() for c in password):
        errors.append("Password must contain an uppercase letter.")
    if settings.PASSWORD_REQUIRE_LOWERCASE and not any(c.islower() for c in password):
        errors.append("Password must contain a lowercase letter.")
    if settings.PASSWORD_REQUIRE_DIGIT and not any(c.isdigit() for c in password):
        errors.append("Password must contain a digit.")
    if settings.PASSWORD_REQUIRE_SPECIAL and not any(
        not c.isalnum() for c in password
    ):
        errors.append("Password must contain a special character.")
    if password.lower() in COMMON_PASSWORDS:
        errors.append("This password is too common.")
    if errors:
        raise ValidationError(errors)
    validate_password(password, user=user)


def set_password(user, raw_password, *, must_change=False):
    validate_password_strength(raw_password, user=user)
    user.set_password(raw_password)
    user.must_change_password = must_change
    user.save(update_fields=["password", "must_change_password"])


# ---------------------------------------------------------------------------
# Login brute-force protection (cache-backed, temporary)
# ---------------------------------------------------------------------------

def _lockout_keys(email, ip):
    base = f"{email}|{ip}"
    return f"auth:fail:{base}", f"auth:block:{base}"


def is_login_blocked(email, ip):
    _fails, block_key = _lockout_keys(email, ip)
    return bool(cache.get(block_key))


def record_login_failure(email, ip):
    fails_key, block_key = _lockout_keys(email, ip)
    window = settings.LOGIN_LOCKOUT_MINUTES * 60
    try:
        fails = cache.get(fails_key, 0) + 1
        cache.set(fails_key, fails, timeout=window)
        if fails >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            cache.set(block_key, True, timeout=window)
            cache.delete(fails_key)
            logger.warning("Login blocked for %s from %s", email, ip)
            return True
    except Exception:  # noqa: BLE001
        logger.exception("Login failure tracking unavailable")
    return False


def clear_login_failures(email, ip):
    fails_key, _ = _lockout_keys(email, ip)
    try:
        cache.delete(fails_key)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user, request, refresh: RefreshToken):
    return UserSession.objects.create(
        user=user,
        refresh_jti=refresh["jti"],
        device=_device_label(_user_agent(request)),
        ip_address=client_ip(request),
        user_agent=_user_agent(request),
        expires_at=timezone.now() + refresh.lifetime,
    )


def blacklist_jti(jti):
    """Blacklist the outstanding refresh token identified by JTI."""
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        token = OutstandingToken.objects.filter(jti=jti).first()
        if token:
            BlacklistedToken.objects.get_or_create(token=token)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to blacklist refresh token %s", jti)


def revoke_session(session):
    session.revoke()
    blacklist_jti(session.refresh_jti)


def revoke_all_sessions(user, exclude=None):
    sessions = user.sessions.filter(revoked_at=None)
    if exclude is not None:
        sessions = sessions.exclude(pk=exclude.pk)
    for session in sessions:
        session.revoke()
        blacklist_jti(session.refresh_jti)


# ---------------------------------------------------------------------------
# Password reset / email verification / invitations
# ---------------------------------------------------------------------------

def request_password_reset(user):
    PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    _, raw = PasswordResetToken.issue(
        user, timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES)
    )
    link = f"{settings.FRONTEND_URL}/reset-password?token={raw}"
    send_mail(
        subject="EMAS password reset",
        message=(
            "A password reset was requested for your EMAS account.\n\n"
            f"Reset link (valid for {settings.PASSWORD_RESET_TTL_MINUTES} minutes):\n{link}\n\n"
            "If you did not request this, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


@transaction.atomic
def reset_password(raw_token, new_password):
    token = PasswordResetToken.find(raw_token)
    if token is None:
        raise BusinessRuleError(
            "Invalid or expired reset token.", code="RESET_TOKEN_INVALID"
        )
    token.consume()
    user = token.user
    set_password(user, new_password)
    revoke_all_sessions(user)
    return user


def send_verification_email(user):
    _, raw = EmailVerificationToken.issue(
        user, timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS)
    )
    link = f"{settings.FRONTEND_URL}/verify-email?token={raw}"
    send_mail(
        subject="Verify your EMAS email",
        message=f"Verify your email address: {link}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


@transaction.atomic
def verify_email(raw_token):
    token = EmailVerificationToken.find(raw_token)
    if token is None:
        raise BusinessRuleError(
            "Invalid or expired verification token.", code="RESET_TOKEN_INVALID"
        )
    token.consume()
    user = token.user
    user.email_verified = True
    if user.status == User.Status.PENDING_VERIFICATION:
        user.status = User.Status.ACTIVE
    user.save(update_fields=["email_verified", "status"])
    return user


@transaction.atomic
def create_invitation(*, email, first_name, last_name, phone, role, school, actor):
    """Admin creates a user + sends a single-use invitation token. The invitee
    sets their own password; no password is ever transmitted."""
    user = User.objects.create_user(
        email=email,
        password=None,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        status=User.Status.PENDING_VERIFICATION,
    )
    invitation, raw = UserInvitation.issue(
        user,
        timedelta(hours=settings.INVITATION_TTL_HOURS),
        invited_by=actor,
        school=school,
        role=role,
    )
    link = f"{settings.FRONTEND_URL}/accept-invitation?token={raw}"
    send_mail(
        subject="You have been invited to EMAS",
        message=(
            "Your EMAS account has been created.\n\n"
            f"Use this link to set your password and activate your account:\n{link}\n\n"
            f"The link expires in {settings.INVITATION_TTL_HOURS} hours."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
    return user, invitation


@transaction.atomic
def accept_invitation(raw_token, new_password):
    invitation = UserInvitation.find(raw_token)
    if invitation is None:
        raise BusinessRuleError(
            "Invalid or expired invitation.", code="RESET_TOKEN_INVALID"
        )
    invitation.consume()
    user = invitation.user
    set_password(user, new_password)
    user.email_verified = True
    user.status = User.Status.ACTIVE
    user.save(update_fields=["email_verified", "status"])
    if invitation.school and invitation.role:
        SchoolMembership.objects.get_or_create(
            user=user, school=invitation.school, defaults={"role": invitation.role}
        )
    return user


def create_owned_organization(user, organization_name):
    """Create a new organization administrated by ``user`` (EXAM_ADMIN role,
    default grading scheme). A user may own several organizations."""
    import uuid as _uuid

    from apps.schools.models import School

    school = School.objects.create(
        school_name=organization_name.strip(),
        school_code=f"ORG-{_uuid.uuid4().hex[:6].upper()}",
    )
    SchoolMembership.objects.create(user=user, school=school, role="EXAM_ADMIN")
    GradeCalculationService.ensure_default_scheme(school)
    return school


@transaction.atomic
def claim_organization(user, organization_name):
    """Attach a user with no active memberships to a new organization they
    administrate. Raises if the user already has one."""
    if user.memberships.filter(is_active=True).exists():
        raise BusinessRuleError(
            "This account already belongs to an organization.",
            code="CONFLICT",
        )
    return create_owned_organization(user, organization_name)


# ---------------------------------------------------------------------------
# Account status changes (admin)
# ---------------------------------------------------------------------------

def set_account_status(user, status, *, actor=None, request=None):
    user.status = status
    user.is_active = status == User.Status.ACTIVE
    user.save(update_fields=["status", "is_active"])
    if status != User.Status.ACTIVE:
        revoke_all_sessions(user)
    action = {
        User.Status.ACTIVE: AuthenticationAuditLog.Action.ACCOUNT_ACTIVATED,
        User.Status.SUSPENDED: AuthenticationAuditLog.Action.ACCOUNT_SUSPENDED,
        User.Status.INACTIVE: AuthenticationAuditLog.Action.ACCOUNT_DEACTIVATED,
    }.get(status)
    if action:
        log_auth_event(action, request=request, user=actor, metadata={"target": str(user.pk)})
