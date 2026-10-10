from django.urls import path

from observability.views import internal_readiness

urlpatterns = [
    path("internal/readiness", internal_readiness, name="internal_readiness"),
]
