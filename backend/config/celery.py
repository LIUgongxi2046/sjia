"""Celery 应用配置。

Redis 仅传消息，业务结果读取 PostgreSQL；Celery result backend 不承载病例结果。
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("sjia")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
