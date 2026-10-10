"""本地批准内容：字典、单位映射、保留处置政策与家庭可见文案（D06/D07/D09）。

这些定义是首版内容，仅用于本地开发与联调，后续按临床与项目负责人反馈调整。
"""
from datetime import datetime, timezone
from decimal import Decimal

from django.core.management.base import BaseCommand

from configurations.models import ConfigVersion
from dictionaries.models import DictionaryEntry, UnitMapping
from identity.models import Principal
from retention.models import RetentionPolicy
from sources.models import SourceRevision

# D07：概念字典（concept_code、类别、中文标签、定义引用）。
DICTIONARY_ENTRIES = [
    ("temp", "vital", "体温", "BT"),
    ("rash", "symptom", "皮疹", "RASH"),
    ("platelet", "laboratory", "血小板", "PLT"),
    ("ast", "laboratory", "天冬氨酸氨基转移酶", "AST"),
    ("triglycerides", "laboratory", "甘油三酯", "TG"),
    ("fibrinogen", "laboratory", "纤维蛋白原", "FIB"),
    ("ferritin", "laboratory", "铁蛋白", "FER"),
    ("ldh", "laboratory", "乳酸脱氢酶", "LDH"),
    ("cns", "clinical", "中枢神经系统受累", "CNS"),
    ("hemorrhagic", "clinical", "出血表现", "HEM"),
    ("arthritis", "clinical", "活动性关节炎", "ART"),
]

# D07：单位映射（概念、原单位 → 归一化单位、系数、加数、仅精确值）。
UNIT_MAPPINGS = [
    ("triglycerides", "mmol/L", "mg/dL", "88.57", "0"),
    ("triglycerides", "mg/dL", "mg/dL", "1", "0"),
    ("fibrinogen", "g/L", "mg/dL", "100", "0"),
    ("fibrinogen", "mg/dL", "mg/dL", "1", "0"),
    ("ferritin", "μg/L", "ng/mL", "1", "0"),
    ("ferritin", "ng/mL", "ng/mL", "1", "0"),
    ("platelet", "10^9/L", "10^9/L", "1", "0"),
    ("ast", "U/L", "U/L", "1", "0"),
    ("ldh", "U/L", "U/L", "1", "0"),
    ("temp", "C", "C", "1", "0"),
]

# D09：保留处置政策（按资料类别，保留天数；None 表示长期保留）。
RETENTION_RULES = {
    "schema_version": "retention-1",
    "classes": {
        "observation": {"retention_days": 3650, "disposition": "delete"},
        "report": {"retention_days": 3650, "disposition": "delete"},
        "therapy": {"retention_days": 3650, "disposition": "delete"},
        "audit": {"retention_days": None, "disposition": "retain"},
    },
}

# D06：家庭可见文案。家庭只看到预警状态，不看到分类结果、总分、贡献与阈值。
VISIBILITY_PAYLOAD = {
    "schema_version": "visibility-1",
    "visible_score": False,
    "alert_disclaimer": "本提示用于预警，不构成诊断，请及时就医。",
    "home_states": {
        "no_data": "暂无记录，请先记录患儿资料。",
        "insufficient": "资料不足，暂无法评估。",
        "current": "存在需要关注的信号，请查看预警并及时就医。",
        "cleared": "当前未发现需要关注的信号。",
    },
}


class Command(BaseCommand):
    help = "建立本地批准内容（字典、单位映射、保留政策与家庭文案）"

    def handle(self, *args, **options):
        now = datetime.now(timezone.utc)
        principal = Principal.objects.filter(account_status="active").first()
        revision = SourceRevision.objects.first()
        if principal is None or revision is None:
            self.stdout.write(self.style.WARNING("缺少基础虚拟数据，先运行 seed_demo"))
            return

        dictionary_config = ConfigVersion.objects.filter(config_key="dictionary-baseline", kind="dictionary").first()
        if dictionary_config is None:
            dictionary_config = ConfigVersion.objects.create(
                config_key="dictionary-baseline", kind="dictionary", version=1,
                schema_version="dictionary-1", payload={"concepts": [e[0] for e in DICTIONARY_ENTRIES]},
                checksum="a" * 64, status=ConfigVersion.Status.ENABLED, created_by=principal, created_at=now,
            )
        for concept_code, category, label, definition_ref in DICTIONARY_ENTRIES:
            if not DictionaryEntry.objects.filter(dictionary_version=dictionary_config, concept_code=concept_code).exists():
                DictionaryEntry.objects.create(
                    dictionary_version=dictionary_config, concept_code=concept_code,
                    category=category, label=label, definition_ref=definition_ref,
                )

        mapping_config = ConfigVersion.objects.filter(config_key="unit-mapping-baseline", kind="mapping").first()
        if mapping_config is None:
            mapping_config = ConfigVersion.objects.create(
                config_key="unit-mapping-baseline", kind="mapping", version=1,
                schema_version="mapping-1", payload={"concepts": list({m[0] for m in UNIT_MAPPINGS})},
                checksum="b" * 64, status=ConfigVersion.Status.ENABLED, created_by=principal, created_at=now,
            )
        for concept_code, raw_unit, normalized_unit, multiplier, additive in UNIT_MAPPINGS:
            if not UnitMapping.objects.filter(
                config=mapping_config, concept_code=concept_code, source_revision=revision,
                raw_unit=raw_unit, method_scope="any",
            ).exists():
                UnitMapping.objects.create(
                    config=mapping_config, concept_code=concept_code, source_revision=revision,
                    raw_unit=raw_unit, normalized_unit=normalized_unit, method_scope="any",
                    multiplier=Decimal(multiplier), additive=Decimal(additive), exact_values_only=False,
                )

        for data_class, rules in RETENTION_RULES["classes"].items():
            policy = RetentionPolicy.objects.filter(policy_key=f"retention-{data_class}").first()
            if policy is None:
                RetentionPolicy.objects.create(
                    policy_key=f"retention-{data_class}", version=1, data_class=data_class,
                    rules=rules, schema_version="retention-1", status=RetentionPolicy.Status.ACTIVE,
                    approved_by=principal, approved_at=now, evidence_ref="d09-first-pass",
                )
            elif policy.rules != rules:
                policy.rules = rules
                policy.save(update_fields=["rules"])

        visibility = ConfigVersion.objects.filter(config_key="visibility-family", kind="visibility").first()
        if visibility is None:
            ConfigVersion.objects.create(
                config_key="visibility-family", kind="visibility", version=1,
                schema_version="visibility-1", payload=VISIBILITY_PAYLOAD,
                checksum="c" * 64, status=ConfigVersion.Status.ENABLED, created_by=principal, created_at=now,
            )

        self.stdout.write(self.style.SUCCESS(
            f"content seeded: dictionary={DictionaryEntry.objects.count()} "
            f"mappings={UnitMapping.objects.count()} "
            f"retention={RetentionPolicy.objects.count()} visibility=1"
        ))
