import redis
from django.conf import settings
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import serializers


class _HealthSerializer(serializers.Serializer):
    status = serializers.CharField(required=False)
    checks = serializers.DictField(required=False)


_HealthSchema = extend_schema(responses={200: _HealthSerializer})


@_HealthSchema
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"})


@_HealthSchema
@api_view(["GET"])
@permission_classes([AllowAny])
def health_live(request):
    return Response({"status": "alive"})


@_HealthSchema
@api_view(["GET"])
@permission_classes([AllowAny])
def health_ready(request):
    checks = {"database": "ok", "redis": "ok"}
    status_code = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
        status_code = 503

    try:
        client = redis.from_url(settings.REDIS_URL, socket_timeout=2)
        client.ping()
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"
        status_code = 503

    return Response({"status": "ready" if status_code == 200 else "degraded", "checks": checks}, status=status_code)
