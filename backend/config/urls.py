from django.urls import include, path

urlpatterns = [
    path("", include("observability.urls")),
    path('api/v2/', include('api.urls_v2')),
    path('api/v1/', include('api.urls')),
    path("api/", include("api.urls")),
]
