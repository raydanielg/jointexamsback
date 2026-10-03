from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core.exceptions import BusinessRuleError

from .models import MEMBER_ROLES, Role, SchoolMembership

MANAGE_ROLES = (Role.EXAM_ADMIN, Role.SCHOOL_COORDINATOR)


def resolve_active_school(request):
    """Resolve and cache (request.school, request.membership).

    JWT authentication happens in DRF's view layer — after Django
    middleware — so school resolution runs lazily here, inside the
    permission phase where request.user is already authenticated.
    """
    if getattr(request, "_school_resolved", False):
        return getattr(request, "school", None), getattr(request, "membership", None)

    request._school_resolved = True
    request.school = getattr(request, "school", None)
    request.membership = getattr(request, "membership", None)

    user = request.user
    if not user or not user.is_authenticated:
        return None, None

    if request.school is not None and request.membership is not None:
        return request.school, request.membership

    school_id = request.headers.get("X-School-ID")
    memberships = SchoolMembership.objects.filter(user=user, is_active=True).select_related("school")

    if school_id:
        if user.is_superadmin:
            from apps.schools.models import School

            request.school = School.objects.filter(pk=school_id).first()
        else:
            membership = memberships.filter(school_id=school_id).first()
            if membership:
                request.membership = membership
                request.school = membership.school
    elif memberships.count() == 1:
        request.membership = memberships.first()
        request.school = request.membership.school
    if request.school is None and memberships.exists():
        # Multi-org users have no pinned scope — pick a default school so
        # write paths have somewhere to attach new records; the payload may
        # still override via ``school``.
        first = memberships.first()
        request.membership = request.membership or first
        request.school = first.school
    return request.school, request.membership


def accessible_school_ids(request):
    """All schools the request's user may act on: their membership schools
    plus any child centers of those. Empty for anonymous users."""
    from apps.schools.models import School

    user = request.user
    if not user or not user.is_authenticated:
        return School.objects.none().values_list("pk", flat=True)
    if user.is_superadmin:
        return School.objects.values_list("pk", flat=True)
    member_ids = user.memberships.filter(is_active=True).values_list("school_id", flat=True)
    return School.objects.filter(
        Q(pk__in=member_ids) | Q(parent_id__in=member_ids)
    ).values_list("pk", flat=True)


def write_school(request):
    """School for write operations: the payload's ``school`` if it is within
    the caller's accessible schools, else the resolved default."""
    from apps.schools.models import School

    requested = request.data.get("school") if hasattr(request, "data") else None
    school = getattr(request, "school", None)
    if requested:
        school = School.objects.filter(pk=requested).filter(
            pk__in=accessible_school_ids(request)
        ).first()
        if school is None:
            raise BusinessRuleError(
                "The selected organization is not accessible.", code="PERMISSION_DENIED"
            )
    if school is None:
        raise BusinessRuleError(
            "Select an organization to save this record under.",
            code="VALIDATION_ERROR",
        )
    return school


def user_exam_membership(request, examination):
    """Return the role a user effectively holds for an examination:

    - superadmin -> SUPER_ADMIN
    - membership on the organizer school -> that role
    - membership on a participating school -> that role (school-scoped
      operations are further restricted by query filters)
    """
    user = request.user
    if user.is_superadmin:
        return Role.SUPER_ADMIN, None
    resolve_active_school(request)
    memberships = SchoolMembership.objects.filter(user=user, is_active=True).select_related("school")
    school_ids = {m.school_id for m in memberships}
    if examination.school_id in school_ids:
        m = next(m for m in memberships if m.school_id == examination.school_id)
        return m.role, m
    for m in memberships:
        if examination.participating_schools.filter(pk=m.school_id).exists():
            return m.role, m
    return None, memberships.first()


def can_manage_exam(request, examination):
    """Only organizer-school EXAM_ADMINs (or super admins) may configure,
    transition, finalize or publish an examination."""
    role, _ = user_exam_membership(request, examination)
    if role == Role.SUPER_ADMIN:
        return True
    if role != Role.EXAM_ADMIN:
        return False
    return examination.school_id in set(
        request.user.memberships.filter(is_active=True).values_list("school_id", flat=True)
    )


class HasActiveSchool(BasePermission):
    message = "An active school is required. Pass the X-School-ID header."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        school, membership = resolve_active_school(request)
        if request.user.is_superadmin:
            return school is not None
        return membership is not None


class RolePermission(BasePermission):
    """School-scoped role enforcement. Views may override ``read_roles`` and
    ``write_roles``. Multi-school examination endpoints additionally use
    ``user_exam_membership`` to scope rows."""

    message = "You do not have the required role for this operation."

    read_roles = MEMBER_ROLES
    write_roles = MANAGE_ROLES

    def required_roles(self, request, view):
        if request.method in SAFE_METHODS:
            return getattr(view, "read_roles", self.read_roles)
        return getattr(view, "write_roles", self.write_roles)

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superadmin:
            return True
        school, membership = resolve_active_school(request)
        if school is None:
            return False
        if membership is None or membership.school_id != school.id:
            return False
        return membership.role in self.required_roles(request, view)


class IsSuperAdmin(BasePermission):
    message = "Super administrator access required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superadmin)


def role_required(*roles):
    class _Role(RolePermission):
        read_roles = roles
        write_roles = roles

    return _Role


class HasPermission(BasePermission):
    """Checks the caller's effective permission codes. Views declare:

        required_permissions = ("exams.view",)

    For unsafe methods a view may override ``required_permissions_write``.
    SUPER_ADMIN always passes.
    """

    message = "You do not have the required permission."
    required_permissions: tuple = ()
    required_permissions_write: tuple = ()

    @classmethod
    def with_permissions(cls, *codes):
        """Return a permission instance enforcing ``codes`` regardless of the
        view's declared required_permissions."""
        class _Scoped(cls):
            required_permissions = tuple(codes)
            required_permissions_write = tuple(codes)

        return _Scoped()

    def _codes(self, request, view):
        write = getattr(view, "required_permissions_write", ())
        read = getattr(view, "required_permissions", self.required_permissions)
        if request.method not in SAFE_METHODS and write:
            return write
        return read or write

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superadmin:
            return True
        codes = self._codes(request, view)
        if not codes:
            return True
        user_perms = user.permission_codes()
        return all(code in user_perms for code in codes)


def permission_required(*codes):
    """Factory: a permission class requiring all given codes."""

    class _Perm(HasPermission):
        required_permissions = codes
        required_permissions_write = codes

    return _Perm


class HasRole(BasePermission):
    """Role check independent of school context. Views may set
    ``required_roles``; SUPER_ADMIN always passes."""

    message = "You do not have the required role."
    required_roles: tuple = ()

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superadmin:
            return True
        roles = getattr(view, "required_roles", self.required_roles)
        if not roles:
            return True
        return bool(set(user.effective_roles()) & set(roles))
