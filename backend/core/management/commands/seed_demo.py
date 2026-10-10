"""本地演示虚拟数据：建立虚拟监护人、患儿、授权、配置与来源。

虚拟数据仅用于本地开发联调，不登记为真实验收。
"""
from datetime import datetime, timedelta, timezone

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal, PrincipalCredential, PrincipalIdentity
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


class Command(BaseCommand):
    help = "建立本地演示虚拟数据"

    def handle(self, *args, **options):
        now = datetime.now(timezone.utc)
        principal = Principal.objects.filter(account_status="active").first()
        if principal is None:
            principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
            PrincipalIdentity.objects.create(principal=principal, provider="local-demo", provider_subject="guardian-1", verified_at=now)
        if not PrincipalCredential.objects.filter(principal=principal).exists():
            PrincipalCredential.objects.create(principal=principal, username="guardian-1", password_hash=make_password("demo-pass-1"), created_at=now, updated_at=now)
        patient = Patient.objects.filter(display_id="demo-child").first()
        if patient is None:
            patient = Patient.objects.create(display_id="demo-child", status="active", created_at=now)
        retention = RetentionPolicy.objects.filter(policy_key="demo-retention").first()
        if retention is None:
            retention = RetentionPolicy.objects.create(policy_key="demo-retention", version=1, data_class="observation", rules={}, schema_version="retention-1", status="active")
        field_config = ConfigVersion.objects.filter(config_key="field-config-observation", kind="content").first()
        if field_config is None:
            field_config = ConfigVersion.objects.create(
                config_key="field-config-observation", kind="content", version=1, schema_version="field-config-1",
                payload={
                    "schema_version": "field-config-1", "kind": "observation",
                    "fields": [
                        {"field_code": "temp", "label": "体温", "value_type": "decimal", "required": True, "writable": True, "allowed_values": [], "allowed_units": ["C"], "dictionary_category": None, "minimum": "30.0", "maximum": "45.0", "max_length": None, "max_occurrences": 1, "time_required": True},
                        {"field_code": "rash", "label": "皮疹", "value_type": "categorical", "required": False, "writable": True, "allowed_values": ["yes", "no"], "allowed_units": [], "dictionary_category": None, "minimum": None, "maximum": None, "max_length": None, "max_occurrences": 1, "time_required": False},
                    ],
                    "watch_field_codes": ["temp"],
                },
                checksum="a" * 64, status="enabled", created_by=principal, created_at=now,
            )
        gate = EnvironmentGate.objects.filter(environment="test").first()
        if gate is None:
            gate = EnvironmentGate.objects.create(environment="test", version=1, enabled=True, updated_at=now)
        # 虚拟支持角色：本地演示授权 config/verification/assessment/manage_relations 服务动作。
        support_actions = ["manage_config", "manage_relation", "verify_field", "read_assessment", "execute_assessment", "manage_grant", "import_case", "manage_disposition", "revoke_verification"]
        for action in support_actions:
            if not ServiceRoleGrant.objects.filter(principal=principal, environment="test", action=action).exists():
                ServiceRoleGrant.objects.create(principal=principal, environment_id="test", action=action, granted_by=principal, evidence_ref="demo", valid_from=now)
        source = SourceRegistry.objects.filter(source_key="demo-source").first()
        if source is None:
            source = SourceRegistry.objects.create(source_key="demo-source", owner="demo", location="local", purpose="family_observation", sensitivity="normal", update_frequency="daily", status="approved")
        revision = SourceRevision.objects.filter(source=source).first()
        if revision is None:
            revision = SourceRevision.objects.create(source=source, version="v1", checksum="c" * 64, license="demo", allowed_use=["family_observation"], status="approved", captured_at=now)
        use_auth = DataUseAuthorization.objects.filter(patient=patient).first()
        if use_auth is None:
            use_auth = DataUseAuthorization.objects.create(patient=patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="demo", approved_by=principal, approved_at=now)
        relation = Relation.objects.filter(patient=patient, guardian=principal, status="active").first()
        if relation is None:
            application = RelationApplication.objects.create(version=1, applicant=principal, patient=patient, evidence_ref="demo", status="approved", checked_by=principal, checked_at=now, created_at=now)
            relation = Relation.objects.create(patient=patient, guardian=principal, application=application, status="active", version=1, checked_by=principal, checked_at=now, valid_from=now)
        for action in ["read_raw", "write_record", "read_history", "read_file", "manage_draft", "receive_alert", "create_summary", "read_summary", "request_correction", "request_disposition", "view_request", "correct_record", "read_case"]:
            if not AccessGrant.objects.filter(patient=patient, principal=principal, action=action).exists():
                AccessGrant.objects.create(patient=patient, principal=principal, relation_id=relation.id, action=action, field_codes=["temp", "rash"], record_scope="all", purpose="family_observation", granted_by=principal, evidence_ref="demo", valid_from=now)
        from django.core.management import call_command

        call_command("seed_content", verbosity=0)
        self.stdout.write(self.style.SUCCESS(f"demo seeded: principal={principal.id} patient={patient.id}"))
