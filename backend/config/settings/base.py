"""后端基础配置。

所有敏感值与运行参数由环境变量注入，缺省值只允许用于明确标注的本地开发项。
启用项缺少配置时在进程启动位置立即报错，不静默降级。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 本地与集成环境的敏感值从 backend/.env 注入（该文件已 gitignore，不提交）。
load_dotenv(BASE_DIR / ".env")
FILE_OBJECT_ROOT = Path(os.environ.get('SJIA_FILE_OBJECT_ROOT') or BASE_DIR.parent / '.backend-work' / 'object-store')
SJIA_DEMO_FONT_PATH = os.environ.get('SJIA_DEMO_FONT_PATH', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
# No production policy, retention period or automatic journal provisioning.
SJIA_CONTENT_DISPOSITION_ENABLED = os.environ.get('SJIA_CONTENT_DISPOSITION_ENABLED') == '1'
SJIA_CONTENT_JOURNAL_ROOT = os.environ.get('SJIA_CONTENT_JOURNAL_ROOT')
SJIA_CONTENT_CHECKPOINT_PATH = os.environ.get('SJIA_CONTENT_CHECKPOINT_PATH')
SJIA_RESTORE_ROOT = os.environ.get('SJIA_RESTORE_ROOT')
SJIA_ENVIRONMENT = os.environ.get('SJIA_ENVIRONMENT')
CONFIG_EVIDENCE_ROOT = os.environ.get('SJIA_CONFIG_EVIDENCE_ROOT')
config_evidence_limit = os.environ.get('SJIA_CONFIG_EVIDENCE_MAX_BYTES')
CONFIG_EVIDENCE_MAX_BYTES = int(config_evidence_limit) if config_evidence_limit else None
if CONFIG_EVIDENCE_MAX_BYTES is not None and CONFIG_EVIDENCE_MAX_BYTES <= 0:
    raise ValueError('SJIA_CONFIG_EVIDENCE_MAX_BYTES 必须大于零')

# ---------------------------------------------------------------------------
# 身份与调试
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SJIA_SECRET_KEY", "")
DEBUG = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("SJIA_ALLOWED_HOSTS", "").split(",")
    if h.strip()
]

# ---------------------------------------------------------------------------
# 应用与中间件
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "core",
    "identity",
    "retention",
    "sources",
    "configurations",
    "dictionaries",
    "relations",
    "authorization",
    "requests",
    "drafts",
    "medical_time",
    "family_content",
    "contacts",
    "tasks",
    "dependencies",
    "audit",
    "records",
    "files",
    "recognition",
    "verification",
    "assessment",
    "alerting",
    "notifications",
    "summaries",
    "case_details",
    "archives",
    "observability",
]

MIDDLEWARE = [
    'observability.middleware.RuntimeObservationMiddleware',
    'retention.content_recovery.RestoreAdmissionMiddleware',
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("SJIA_DB_NAME", ""),
        "USER": os.environ.get("SJIA_DB_USER", ""),
        "PASSWORD": os.environ.get("SJIA_DB_PASSWORD", ""),
        "HOST": os.environ.get("SJIA_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("SJIA_DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("SJIA_DB_CONN_MAX_AGE", "60")),
        "OPTIONS": {
            "connect_timeout": 3,
            # Historical V2 SQL changes search_path while migrating. Django's
            # session/auth tables may therefore live in sjia on a fresh install;
            # also retain compatibility with existing tables in public.
            "options": "-c search_path=public,sjia -c lock_timeout=5000 -c statement_timeout=30000",
        },
    }
}

# ---------------------------------------------------------------------------
# 会话与 CSRF（依据 backend-design/runtime.schema.json 与 handshake 第 5 节）
# ---------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "__Host-sjia_session"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_USE_SESSIONS = True
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# 国际化
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# 静态资源
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "UNAUTHENTICATED_USER": None,
}

# ---------------------------------------------------------------------------
# 任务与缓存（Redis Broker；Worker 由 T06 建立）
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("SJIA_REDIS_URL", "")
CELERY_BROKER_URL = os.environ.get("SJIA_CELERY_BROKER_URL", "")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_IGNORE_RESULT = True
CELERY_IMPORTS = ('tasks.worker', 'tasks.dispatcher', 'retention.jobs', 'alerting.triggers', 'notifications.material_scheduler')
CELERY_BEAT_SCHEDULE = {'dispatch-outbox': {'task': 'tasks.dispatcher.dispatch_pending', 'schedule': 2.0, 'options': {'queue': 'default'}}}
CELERY_BEAT_SCHEDULE['dispatch-purpose-outbox'] = {'task': 'tasks.dispatcher.dispatch_pending', 'schedule': 2.0,
    'kwargs': {'event_types': ['purpose_copy.requested', 'disposition.local_requested', 'disposition.provider_requested'], 'owner': 'purpose-dispatcher'}, 'options': {'queue': 'purpose'}}
CELERY_BEAT_SCHEDULE['reconcile-local-dispositions'] = {'task': 'retention.jobs.reconcile_local_dispositions',
    'schedule': 2.0, 'options': {'queue': 'purpose'}}
CELERY_BEAT_SCHEDULE['schedule-dynamic-evaluations'] = {'task': 'alerting.triggers.schedule_due',
    'schedule': 2.0, 'options': {'queue': 'assessment'}}
CELERY_BEAT_SCHEDULE['schedule-material-reminders'] = {'task': 'notifications.material_scheduler.schedule_due',
    'schedule': 2.0, 'options': {'queue': 'notification'}}

# ---------------------------------------------------------------------------
# 外部提供方（敏感密钥只从环境注入，不落入代码、日志或版本库）
# ---------------------------------------------------------------------------
# OCR 提供方：报告识别。密钥与端点写入 backend/.env（模板见 backend/.env.example）。
OCR_API_KEY = os.environ.get("SJIA_OCR_API_KEY", "")
OCR_API_URL = os.environ.get("SJIA_OCR_API_URL", "")
OCR_MODEL = os.environ.get("SJIA_OCR_MODEL", "")

# 大模型提供方（火山方舟 Ark）：语音转文字后的结构化快速记录。
# 密钥与模型写入 backend/.env；接口为 OpenAI 兼容。
ARK_API_KEY = os.environ.get("SJIA_ARK_API_KEY", "")
ARK_BASE_URL = os.environ.get("SJIA_ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
ARK_MODEL = os.environ.get("SJIA_ARK_MODEL", "")
ARK_VISION_MODEL = os.environ.get("SJIA_ARK_VISION_MODEL", "")
ARK_VISION_MAX_TOKENS = int(os.environ.get('SJIA_ARK_VISION_MAX_TOKENS', '4096'))
ARK_REQUEST_TIMEOUT_SECONDS = int(os.environ.get('SJIA_ARK_REQUEST_TIMEOUT_SECONDS', '120'))
if ARK_REQUEST_TIMEOUT_SECONDS <= 0:
    raise ValueError('SJIA_ARK_REQUEST_TIMEOUT_SECONDS 必须大于零')
SPEECH_MODEL_DIR = os.environ.get('SJIA_SPEECH_MODEL_DIR', '')


def assert_runtime_config() -> None:
    """在进程启动位置核验启用项配置，缺配置立即报错。

    仅用于本地开发调试的项不在此处强制；生产与集成环境由运行时配置
    （T04/T06 的 runtime.json）另行加载和校验。此处保护最基本的进程身份。
    """
    missing = []
    if not SECRET_KEY:
        missing.append("SJIA_SECRET_KEY")
    if not ALLOWED_HOSTS:
        missing.append("SJIA_ALLOWED_HOSTS")
    if not SJIA_ENVIRONMENT:
        missing.append("SJIA_ENVIRONMENT")
    if not DATABASES["default"]["NAME"]:
        missing.append("SJIA_DB_NAME")
    if not DATABASES["default"]["USER"]:
        missing.append("SJIA_DB_USER")
    if missing:
        raise RuntimeError(
            "缺少必需的运行配置，进程拒绝启动：" + ", ".join(missing)
        )

# Runtime observations contain only trace ID, operation name, status and elapsed time.
# Internal peers use the socket address, never caller-supplied forwarding headers.
INTERNAL_PROBE_NETWORKS = [value.strip() for value in os.environ.get(
    'SJIA_INTERNAL_PROBE_NETWORKS', '127.0.0.0/8,::1/128').split(',') if value.strip()]
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'formatters': {'sjia_runtime': {'()': 'observability.middleware.SafeRuntimeFormatter'}},
    'handlers': {'sjia_runtime': {'class': 'logging.StreamHandler', 'formatter': 'sjia_runtime'}},
    'loggers': {'sjia.runtime': {'handlers': ['sjia_runtime'], 'level': 'INFO', 'propagate': False}},
}

# Management commands and development imports do not require an admitted runtime.
REQUIRE_PROCESS_RUNTIME_ADMISSION = False
