"""Pluggable SMS providers.

Configure via ``SMS_PROVIDER_BACKEND`` (dotted path to a provider class, or
one of the built-in names). Credentials come from environment variables,
never from code.
"""
import logging
import urllib.request
from importlib import import_module
from urllib.parse import urlencode

from django.conf import settings

logger = logging.getLogger("emas.sms")


class BaseSMSProvider:
    name = "base"

    def send_sms(self, phone, message):  # pragma: no cover - interface
        """Return (success: bool, provider_message_id: str, error: str)."""
        raise NotImplementedError

    def get_balance(self):
        return None

    def check_status(self, provider_message_id):
        return None


class ConsoleSMSProvider(BaseSMSProvider):
    """Development provider: logs instead of sending."""

    name = "console"

    def send_sms(self, phone, message):
        logger.info("[SMS:console] to=%s msg=%s", phone, message)
        return True, f"console-{phone}", ""


class WebhookSMSProvider(BaseSMSProvider):
    """Generic HTTP gateway: POST {to, message} to ``SMS_WEBHOOK_URL`` with
    optional ``SMS_WEBHOOK_TOKEN`` Bearer auth. Adapts most local gateways."""

    name = "webhook"

    def __init__(self):
        self.url = getattr(settings, "SMS_WEBHOOK_URL", "")
        self.token = getattr(settings, "SMS_WEBHOOK_TOKEN", "")

    def send_sms(self, phone, message):
        if not self.url:
            return False, "", "SMS webhook URL not configured."
        payload = urlencode({"to": phone, "message": message}).encode()
        req = urllib.request.Request(self.url, data=payload)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300, f"webhook-{phone}", ""
        except Exception as exc:  # noqa: BLE001
            return False, "", str(exc)[:500]


def get_provider():
    backend = getattr(settings, "SMS_PROVIDER_BACKEND", "console")
    builtin = {"console": ConsoleSMSProvider, "webhook": WebhookSMSProvider}
    if backend in builtin:
        return builtin[backend]()
    module_path, class_name = backend.rsplit(".", 1)
    cls = getattr(import_module(module_path), class_name)
    return cls()
