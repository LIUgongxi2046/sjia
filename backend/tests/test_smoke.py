"""T01 工程环境冒烟检查。

验证 Django 进程可以启动、连接真实 PostgreSQL、并携带批准的会话与 CSRF 配置。
运行命令：backend/.venv/bin/python backend/manage.py test tests.test_smoke --keepdb
"""
from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase


class DatabaseConnectivityTest(SimpleTestCase):
    databases = {"default"}

    def test_postgres_reachable(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))

    def test_database_engine_is_postgresql(self):
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql")


class SessionCsrfConfigTest(SimpleTestCase):
    def test_session_cookie_name_matches_runtime_schema(self):
        from config.settings import base as base_settings

        # 目标环境（HTTPS）使用 __Host- 前缀；本地 HTTP 开发在 development.py 去掉前缀，
        # 因为 __Host- 前缀要求 Secure，浏览器与 curl 在 HTTP 下会拒绝该 Cookie。
        self.assertEqual(base_settings.SESSION_COOKIE_NAME, "__Host-sjia_session")
        self.assertEqual(settings.SESSION_COOKIE_NAME, "sjia_session")

    def test_session_cookie_is_http_only(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_csrf_uses_sessions(self):
        self.assertTrue(settings.CSRF_USE_SESSIONS)

    def test_csrf_header_name(self):
        self.assertEqual(settings.CSRF_HEADER_NAME, "HTTP_X_CSRFTOKEN")

    def test_timezone(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Shanghai")
        self.assertTrue(settings.USE_TZ)


class FailFastConfigTest(SimpleTestCase):
    def test_assert_runtime_config_raises_when_required_missing(self):
        from config.settings import base

        # 本地 .env 已提供必需配置；临时清空 SECRET_KEY 验证缺配置立即报错，再恢复。
        original_secret = base.SECRET_KEY
        base.SECRET_KEY = ""
        try:
            with self.assertRaises(RuntimeError):
                base.assert_runtime_config()
        finally:
            base.SECRET_KEY = original_secret
