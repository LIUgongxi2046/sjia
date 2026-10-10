from pathlib import Path

from dotenv import load_dotenv


SIMULATION_ROOT = Path(__file__).resolve().parents[3] / '.simulation-work'
if not (SIMULATION_ROOT / 'environment.env').is_file():
    raise RuntimeError('模拟环境尚未初始化，请运行 deploy/initialize_simulation.py')
load_dotenv(SIMULATION_ROOT / 'environment.env', override=True)

from .development import *  # noqa: E402,F401,F403


SJIA_ENVIRONMENT = 'simulation'
DATABASES['default']['NAME'] = 'sjia_simulation'
DATABASES['default']['OPTIONS'] = {'options': '-c search_path=sjia,public -c lock_timeout=5000 -c statement_timeout=30000'}
FILE_OBJECT_ROOT = SIMULATION_ROOT / 'object-store'
CONFIG_EVIDENCE_ROOT = str(SIMULATION_ROOT / 'config-evidence')
CONFIG_EVIDENCE_MAX_BYTES = 10485760
SESSION_COOKIE_NAME = 'sjia_simulation_session'
CSRF_COOKIE_NAME = 'sjia_simulation_csrf'
REDIS_URL = 'redis://127.0.0.1:6379/14'
CELERY_BROKER_URL = 'redis://127.0.0.1:6379/15'
CELERY_BROKER_TRANSPORT_OPTIONS = {'global_keyprefix': 'sjia-simulation:'}
CELERY_TASK_DEFAULT_QUEUE = 'simulation-default'
CELERY_BEAT_SCHEDULE = {}
CELERY_BEAT_SCHEDULE_FILENAME = str(SIMULATION_ROOT / 'celerybeat-schedule')

if not DEBUG or DATABASES['default']['HOST'] not in ('127.0.0.1', 'localhost'):
    raise RuntimeError('模拟环境仅允许本地开发服务')
if not SECRET_KEY or len(SECRET_KEY.encode()) < 32:
    raise RuntimeError('模拟环境需要独立签名密钥')
