"""本地开发配置。

仅用于本地开发；不会关闭安全相关的会话与 CSRF 设置。
"""
from .base import *  # noqa: F401,F403

if SJIA_ENVIRONMENT is None:
    SJIA_ENVIRONMENT = 'test'

DEBUG = os.environ.get("SJIA_DEBUG", "1") == "1"

if DEBUG:
    # 本地 HTTPS 不可用时 Session/CSRF Cookie 需要允许明文，仅在开发环境放宽。
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    # 开发环境运行在 http://127.0.0.1 时 Secure 标记会阻止 Cookie 写入。
    # __Host- 前缀要求 Secure 属性，HTTP 本地开发会被浏览器与 curl 拒绝，
    # 本地改用无前缀名称；目标环境保持 base.py 的 __Host- 前缀与 Secure。
    SESSION_COOKIE_NAME = "sjia_session"

if not SECRET_KEY:
    SECRET_KEY = "dev-insecure-secret-key-not-for-production"

if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

if not DATABASES["default"]["NAME"]:
    DATABASES["default"].update(
        {
            "NAME": "sjia_dev",
            "USER": "sjia",
            "PASSWORD": os.environ.get("SJIA_DB_PASSWORD", ""),
            "HOST": "127.0.0.1",
            "PORT": "5433",
        }
    )

if not REDIS_URL:
    REDIS_URL = "redis://127.0.0.1:6379/0"
CELERY_BROKER_URL = os.environ.get("SJIA_CELERY_BROKER_URL", REDIS_URL)
