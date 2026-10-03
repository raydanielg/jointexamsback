import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf_exc
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("emas")


class BusinessRuleError(Exception):
    """Domain-level rule violation -> HTTP 400."""

    status_code = 400
    code = "BUSINESS_RULE_VIOLATION"

    def __init__(self, message, *, code=None, details=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class ConflictError(BusinessRuleError):
    status_code = 409
    code = "CONFLICT"


class NotFoundError(BusinessRuleError):
    status_code = 404
    code = "NOT_FOUND"


CODE_MAP = {
    "ValidationError": "VALIDATION_ERROR",
    "NotAuthenticated": "NOT_AUTHENTICATED",
    "AuthenticationFailed": "AUTHENTICATION_FAILED",
    "PermissionDenied": "PERMISSION_DENIED",
    "NotFound": "NOT_FOUND",
    "MethodNotAllowed": "METHOD_NOT_ALLOWED",
    "Throttled": "RATE_LIMITED",
    "ParseError": "PARSE_ERROR",
    "UnsupportedMediaType": "UNSUPPORTED_MEDIA_TYPE",
}


def _extract_message(detail):
    if isinstance(detail, (list, tuple)) and detail:
        return str(detail[0])
    if isinstance(detail, dict):
        for value in detail.values():
            return _extract_message(value)
        return "Request failed."
    return str(detail)


def emas_exception_handler(exc, context):
    if isinstance(exc, BusinessRuleError):
        return Response(
            {
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            },
            status=exc.status_code,
        )

    if isinstance(exc, Http404):
        exc = drf_exc.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exc.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled exception", exc_info=exc)
        return Response(
            {
                "success": False,
                "error": {
                    "code": "SERVER_ERROR",
                    "message": "An unexpected error occurred.",
                    "details": {},
                },
            },
            status=500,
        )

    code = CODE_MAP.get(type(exc).__name__, "ERROR")
    if isinstance(exc, drf_exc.ValidationError):
        codes = exc.get_codes() if hasattr(exc, "get_codes") else None
        if isinstance(codes, dict) and "non_field_errors" in codes:
            code = "VALIDATION_ERROR"

    response.data = {
        "success": False,
        "error": {
            "code": code,
            "message": _extract_message(getattr(exc, "detail", "Request failed.")),
            "details": response.data,
        },
    }
    return response
