from .development import *  # noqa: F401,F403

DATABASES['default']['NAME'] = 'sjia_qa03'
DATABASES['default']['TEST'] = {'NAME': 'test_sjia_qa03'}
FILE_OBJECT_ROOT = BASE_DIR.parent / '.qa-work' / 'QA03' / 'object-store'
CELERY_BROKER_URL = 'redis://127.0.0.1:6379/3'
SPEECH_MODEL_DIR = str(BASE_DIR.parent / '.qa-work' / 'QA04' / 'models' / 'small')
