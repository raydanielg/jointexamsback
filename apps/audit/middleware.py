from .context import set_request


class AuditContextMiddleware:
    """Stores the current request in thread-local storage so services can
    attach actor/IP context to audit entries without threading the request
    through every call signature."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request(request)
        try:
            return self.get_response(request)
        finally:
            set_request(None)
