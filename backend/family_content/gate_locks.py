from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from family_content.models import EnvironmentGate


def read_environment_gate_shared(environment, *, using='default'):
    connection = connections[using]
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise ImproperlyConfigured('环境读取锁要求 PostgreSQL 及有效事务。')
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT environment, version, publication_epoch, enabled, active_release_id, '
            'runtime_config_id, updated_at '
            'FROM "sjia"."environment_gate" WHERE environment = %s FOR SHARE',
            [environment],
        )
        row = cursor.fetchone()
    if row is None:
        raise EnvironmentGate.DoesNotExist(environment)
    return EnvironmentGate(
        environment=row[0], version=row[1], publication_epoch=row[2], enabled=row[3],
        active_release_id=row[4], runtime_config_id=row[5], updated_at=row[6],
    )
