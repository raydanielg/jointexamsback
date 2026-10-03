import threading

_local = threading.local()


def set_request(request):
    _local.request = request


def get_request():
    return getattr(_local, "request", None)


def get_client_ip(request):
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
