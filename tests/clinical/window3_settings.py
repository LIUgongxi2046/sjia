from pathlib import Path

from config.settings.development import *


WINDOW3_ROOT = Path(__file__).resolve().parents[2] / '.planning-work' / 'parallel' / 'window3'
SJIA_ENVIRONMENT = 'window3_simulation'
DEBUG = True
SECRET_KEY = 'window3-simulation-only-secret-key-not-for-real-data'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
DATABASES['default'].update({
    'NAME': 'sjia_simulation',
    'USER': 'window3_owner',
    'PASSWORD': '',
    'HOST': '127.0.0.1',
    'PORT': '5545',
    'CONN_MAX_AGE': 0,
    'OPTIONS': {
        'options': '-c search_path=sjia,public -c lock_timeout=5000 -c statement_timeout=30000',
    },
})
FILE_OBJECT_ROOT = WINDOW3_ROOT / 'object-store'
CONFIG_EVIDENCE_ROOT = str(WINDOW3_ROOT / 'config-evidence')
CONFIG_EVIDENCE_MAX_BYTES = 10485760
SESSION_COOKIE_NAME = 'sjia_window3_simulation_session'
CSRF_COOKIE_NAME = 'sjia_window3_simulation_csrf'
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
REDIS_URL = 'redis://127.0.0.1:6385/14'
CELERY_BROKER_URL = 'redis://127.0.0.1:6385/15'
CELERY_BROKER_TRANSPORT_OPTIONS = {'global_keyprefix': 'sjia-window3-simulation:'}
CELERY_TASK_DEFAULT_QUEUE = 'window3-simulation-default'
CELERY_BEAT_SCHEDULE = {}
CELERY_BEAT_SCHEDULE_FILENAME = str(WINDOW3_ROOT / 'celerybeat-schedule')
