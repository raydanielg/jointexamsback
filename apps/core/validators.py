from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

from .exceptions import BusinessRuleError


def validate_file_size(file: UploadedFile, max_bytes=None):
    limit = max_bytes or settings.MAX_UPLOAD_SIZE_BYTES
    if file.size > limit:
        raise BusinessRuleError(
            f"File too large. Maximum allowed size is {limit // (1024 * 1024)} MB.",
            code="FILE_TOO_LARGE",
        )


def validate_image_file(file: UploadedFile):
    validate_file_size(file)
    content_type = getattr(file, "content_type", "") or ""
    if content_type not in settings.ALLOWED_IMAGE_TYPES:
        raise BusinessRuleError(
            "Unsupported image type. Allowed: JPEG, PNG, WebP.",
            code="INVALID_FILE_TYPE",
        )
    try:
        from PIL import Image

        file.seek(0)
        img = Image.open(file)
        img.verify()
        file.seek(0)
    except Exception:
        raise BusinessRuleError("Uploaded file is not a valid image.", code="INVALID_FILE")


def validate_spreadsheet_file(file: UploadedFile):
    validate_file_size(file, settings.MAX_IMPORT_SIZE_BYTES)
    name = (file.name or "").lower()
    if not (name.endswith(".csv") or name.endswith(".xlsx")):
        raise BusinessRuleError(
            "Only CSV and XLSX files are accepted.", code="INVALID_FILE_TYPE"
        )
    if name.endswith(".xlsx"):
        # XLSX is a zip archive; verify magic bytes instead of trusting extension.
        head = file.read(4)
        file.seek(0)
        if not head.startswith(b"PK"):
            raise BusinessRuleError(
                "Uploaded file is not a valid XLSX document.", code="INVALID_FILE"
            )


def validate_csv_or_xlsx(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("csv", "xlsx"):
        raise ValidationError("File must be CSV or XLSX.")
    return ext
