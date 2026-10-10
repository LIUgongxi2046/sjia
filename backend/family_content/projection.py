"""家庭投影：只返回获准字段，未批准字段不存在于响应中。

未获批准的家庭字段直接过滤，不返回 null 占位。
"""
from collections.abc import Iterable


def project_fields(fields: Iterable[dict], approved_field_codes: Iterable[str]) -> list[dict]:
    approved = set(approved_field_codes)
    return [field for field in fields if field.get("field_code") in approved]


def project_field_codes(field_codes: Iterable[str], approved_field_codes: Iterable[str]) -> list[str]:
    approved = set(approved_field_codes)
    return [code for code in field_codes if code in approved]
