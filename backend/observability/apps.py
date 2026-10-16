from django.apps import AppConfig


class ObservabilityConfig(AppConfig):
    name = "observability"
    label = "observability"
    verbose_name = "运行观测"

    def ready(self):
        from observability.tracing import register_signals
        register_signals()
