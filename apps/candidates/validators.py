import re

from django.core.exceptions import ValidationError

PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def normalize_phone(value):
    """Normalize a phone to a plain international-style number."""
    return re.sub(r"[\s\-()]", "", value or "")


def validate_phone_number(value):
    normalized = normalize_phone(value)
    if not PHONE_RE.match(normalized):
        raise ValidationError("Invalid phone number.")
    return normalized
