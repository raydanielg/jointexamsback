from rest_framework.response import Response


def ok(data=None, message=None, status=200):
    payload = {"success": True, "data": data if data is not None else {}}
    if message:
        payload["message"] = message
    return Response(payload, status=status)


def created(data=None, message=None):
    return ok(data, message, status=201)
