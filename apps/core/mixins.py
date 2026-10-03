from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError


class SchoolScopedQuerySetMixin:
    """Filter a viewset's queryset to the request's active school.

    Models must expose a direct ``school`` FK, or the view may set
    ``school_lookup`` (e.g. ``"list__school"``).
    """

    school_lookup = "school"

    def get_queryset(self):
        qs = super().get_queryset()
        from apps.accounts.permissions import resolve_active_school

        resolve_active_school(self.request)
        if self.request.user.is_authenticated:
            from apps.accounts.permissions import accessible_school_ids

            return qs.filter(**{f"{self.school_lookup}__in": accessible_school_ids(self.request)})
        return qs.none()


def model_save(serializer_or_form):
    """Validate then save a model instance, translating model
    ValidationError into a DRF validation error."""
    instance = serializer_or_form
    try:
        instance.full_clean()
    except ValidationError as exc:
        raise DRFValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    instance.save()
    return instance
