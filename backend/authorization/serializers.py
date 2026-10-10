"""受保护字段拒绝：额外字段直接拒绝，不依赖反序列化忽略保护字段。

家庭输入不能提供 patient_id、author_id、verifier_id、verification_status、
quality_status、rule_result、approved_by 或 permissions。
"""
from rest_framework import serializers

PROTECTED_FIELDS = frozenset(
    {
        "patient_id",
        "author_id",
        "verifier_id",
        "verification_status",
        "quality_status",
        "rule_result",
        "approved_by",
        "permissions",
    }
)


class RejectUnknownFieldsMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data.keys()) - set(self.fields.keys())
            if unknown:
                raise serializers.ValidationError(
                    {field: "unexpected field" for field in sorted(unknown)}
                )
        return super().to_internal_value(data)


class RejectProtectedFieldsMixin(RejectUnknownFieldsMixin):
    pass
