from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from identity.models import Principal


def read_principal_shared(principal_id, *, using='default'):
    connection = connections[using]
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise ImproperlyConfigured('账号读取锁要求 PostgreSQL 及有效事务。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT id FROM "sjia"."principal" WHERE id = %s FOR SHARE',
            [principal_id])
        row = cursor.fetchone()
    if row is None:
        raise Principal.DoesNotExist(principal_id)
    return Principal.objects.using(using).get(id=row[0])
