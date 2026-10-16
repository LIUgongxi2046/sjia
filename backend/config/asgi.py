import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()

# Serving-process admission is deliberately absent from manage.py/AppConfig.
from config.runtime_admission import admit_configured_process
admit_configured_process("api")
