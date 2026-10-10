"""事务边界与隔离级别。

纯查询在短 REPEATABLE READ READ ONLY 事务内完成授权、字段选择与序列化；
写入使用固定锁顺序（environment_gate → 患儿 → principal → 授权 → 意图 → 业务对象）。
"""
from contextlib import contextmanager

from django.db import connection, transaction


@contextmanager
def repeatable_read_readonly():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield
