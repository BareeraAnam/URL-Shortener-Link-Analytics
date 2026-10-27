from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def health_check(request):
    """Liveness/readiness probe for load balancers and Kubernetes."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    try:
        cache.set("health:probe", "1", 5)
        checks["cache"] = "ok" if cache.get("health:probe") == "1" else "error"
    except Exception:  # noqa: BLE001
        checks["cache"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )
