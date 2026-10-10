import os
from pathlib import Path

from config.settings.simulation import *


ROOT = Path(__file__).resolve().parents[2]
WINDOW_ROOT = Path(os.environ['WINDOW4_SIMULATION_ROOT']).resolve()
EXPECTED_PARENT = (ROOT / '.planning-work' / 'parallel' / 'window4').resolve()

if WINDOW_ROOT.parent != EXPECTED_PARENT or not WINDOW_ROOT.name.startswith('isolated'):
    raise RuntimeError('窗口四隔离模拟目录与专用范围不符。')

SIMULATION_ROOT = WINDOW_ROOT
DATABASES['default']['HOST'] = '127.0.0.1'
DATABASES['default']['PORT'] = os.environ['WINDOW4_SIMULATION_DB_PORT']
FILE_OBJECT_ROOT = SIMULATION_ROOT / 'object-store'
CONFIG_EVIDENCE_ROOT = str(SIMULATION_ROOT / 'config-evidence')
BROKER_PORT = os.environ['WINDOW4_SIMULATION_BROKER_PORT']
REDIS_URL = f'redis://127.0.0.1:{BROKER_PORT}/0'
CELERY_BROKER_URL = f'redis://127.0.0.1:{BROKER_PORT}/1'
CELERY_BROKER_TRANSPORT_OPTIONS = {'global_keyprefix': f'window4-{WINDOW_ROOT.name}:'}
CELERY_TASK_DEFAULT_QUEUE = f'window4-{WINDOW_ROOT.name}'
