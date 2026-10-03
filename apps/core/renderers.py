from rest_framework.renderers import JSONRenderer


class EnvelopedJSONRenderer(JSONRenderer):
    """Wrap successful responses in a consistent envelope:

        {"success": true, "data": ..., "message": "..."}

    Responses that already contain a top-level "success" key (error
    responses produced by the exception handler, envelopes built by the
    pagination class or explicit api_response helpers) are passed through
    untouched.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is not None and isinstance(data, dict) and "success" in data:
            return super().render(data, accepted_media_type, renderer_context)

        view = (renderer_context or {}).get("view")
        response = (renderer_context or {}).get("response")
        message = getattr(view, "response_message", None)

        if response is not None and response.status_code >= 400:
            payload = {
                "success": False,
                "error": {
                    "code": "ERROR",
                    "message": "Request failed.",
                    "details": data,
                },
            }
        else:
            payload = {"success": True, "data": data}
            if message:
                payload["message"] = message
        return super().render(payload, accepted_media_type, renderer_context)
