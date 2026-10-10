from decimal import Decimal

from django.db import models


class FiniteDecimalField(models.Field):
    description = "PostgreSQL numeric domain 排除 NaN/Infinity"

    def db_type(self, connection):
        return "sjia.finite_decimal"

    def get_internal_type(self):
        return "DecimalField"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def get_prep_value(self, value):
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class Sha256HexField(models.CharField):
    description = "sha256_hex domain：64 位小写十六进制文本"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 64)
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        return "sjia.sha256_hex"


class LocalTimestampField(models.DateTimeField):
    description = "无时区 timestamp：医学当地时间，时区单独保存"

    def db_type(self, connection):
        return "timestamp"

    def get_prep_value(self, value):
        local = self.to_python(value)
        if local is not None and local.utcoffset() is not None:
            raise ValueError('医学当地时间必须单独保存时区。')
        return local
