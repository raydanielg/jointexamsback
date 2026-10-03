import logging

from .context import get_client_ip, get_request
from .models import AuditLog

logger = logging.getLogger("emas.audit")


def log_action(actor=None, action="", entity=None, school=None, metadata=None, request=None):
    """Write an audit entry. Never raise: auditing must not break the
    business operation it records."""
    try:
        request = request or get_request()
        if school is None and entity is not None:
            school = getattr(entity, "school", None)
        AuditLog.objects.create(
            actor=actor if getattr(actor, "is_authenticated", True) else None,
            school=school,
            action=action,
            entity_type=entity._meta.label_lower if entity is not None else "",
            entity_id=str(getattr(entity, "pk", "") or ""),
            metadata=metadata or {},
            ip_address=get_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
        )
    except Exception:
        logger.exception("Failed to write audit log for action %s", action)
