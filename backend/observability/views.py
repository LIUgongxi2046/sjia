from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from observability.health import readiness


@api_view(["GET"])
def internal_readiness(request):
    environment = request.query_params.get("environment") or "test"
    result = readiness(environment)
    code = status.HTTP_200_OK if result["status"] == "ready" else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(result, status=code)
