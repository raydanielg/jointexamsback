class ActiveSchoolMiddleware:
    """Initializes school-scoping attributes on the request.

    The actual resolution runs lazily in
    ``accounts.permissions.resolve_active_school`` during DRF's permission
    phase, because JWT authentication only happens inside the view layer —
    after middleware has run.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.school = None
        request.membership = None
        return self.get_response(request)
