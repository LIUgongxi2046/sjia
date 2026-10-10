from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from retention.models import RetentionPolicy


def read_retention_policy_shared(policy_id, *, using='default'):
    connection = connections[using]
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise ImproperlyConfigured('保留策略读取要求 PostgreSQL 及有效事务。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT id FROM "sjia"."retention_policy" WHERE id = %s FOR SHARE', [policy_id])
        row = cursor.fetchone()
    if row is None:
        raise RetentionPolicy.DoesNotExist(policy_id)
    return RetentionPolicy.objects.using(using).get(id=row[0])
