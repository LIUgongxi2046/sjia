from functools import wraps
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from observability.health import readiness
from observability.metrics import render_metrics


def internal_only(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            peer = ip_address(request.META.get('REMOTE_ADDR', ''))
            allowed = any(peer in ip_network(value) for value in settings.INTERNAL_PROBE_NETWORKS)
        except ValueError:
            allowed = False
        if not allowed:
            return HttpResponse(status=404)
        response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        return response
    return wrapped


@require_GET
@internal_only
def internal_liveness(request):
    return JsonResponse({'status': 'alive', 'scope': 'process_only'})


@require_GET
@internal_only
def internal_readiness(request):
    # Do not allow a caller to probe another configured environment via a URL parameter.
    result = readiness(settings.SJIA_ENVIRONMENT)
    return JsonResponse(result, status=200 if result['status'] == 'ready' else 503)


@require_GET
@internal_only
def internal_capabilities(request):
    result = readiness(settings.SJIA_ENVIRONMENT)
    return JsonResponse({'runtime': result, 'capabilities': result.get('capabilities', {}),
        'authorization_scope': 'configuration_only_current_actor_patient_purpose_still_required',
        'clinical_qualification': 'NOT_VERIFIED_BY_HEALTH_PROBE'},
        status=200 if result['status'] == 'ready' else 503)


@require_GET
@internal_only
def internal_metrics(request):
    body, available = render_metrics()
    return HttpResponse(body, status=200 if available else 503,
        content_type='text/plain; version=0.0.4; charset=utf-8')
