"""运行健康检查：数据库、Broker 与 loaded_config_version。

ready 需要数据库与 Redis 均可达；loaded_config_version 与实际行为一致。
日志只记录必要标识与错误类型，不记录病例正文或秘密。
"""
import redis
from django.conf import settings
from django.db import connection

from configurations.service import loaded_config_version
from family_content.models import EnvironmentGate


def check_database() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


def check_broker() -> bool:
    client = redis.Redis.from_url(settings.REDIS_URL)
    return bool(client.ping())


def readiness(environment: str | None = None) -> dict:
    database_ok = check_database()
    broker_ok = check_broker()
    config = None
    gate = EnvironmentGate.objects.filter(environment=environment or "test").first()
    if gate is not None:
        config = loaded_config_version(gate)
    return {
        "status": "ready" if database_ok and broker_ok else "not_ready",
        "dependencies": {"database": database_ok, "broker": broker_ok},
        "loaded_config_version": config,
    }
