"""Celery 应用配置。

Redis 仅传消息，业务结果读取 PostgreSQL；Celery result backend 不承载病例结果。
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("sjia")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Signals run only when actual workers/Beat start, never on migration imports.
from celery.signals import worker_init, worker_process_init, beat_init
from config.runtime_admission import celery_process_start, celery_beat_start
worker_init.connect(celery_process_start, weak=False)
worker_process_init.connect(celery_process_start, weak=False)
beat_init.connect(celery_beat_start, weak=False)
