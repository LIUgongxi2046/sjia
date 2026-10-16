from django.urls import path
from observability.views import internal_capabilities, internal_liveness, internal_metrics, internal_readiness

urlpatterns = [
    path('internal/liveness', internal_liveness, name='internal_liveness'),
    path('internal/readiness', internal_readiness, name='internal_readiness'),
    path('internal/capabilities', internal_capabilities, name='internal_capabilities'),
    path('internal/metrics', internal_metrics, name='internal_metrics'),
]
