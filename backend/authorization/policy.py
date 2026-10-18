"""Policy Gate：逐主体、患儿、动作、字段、用途与环境判定真实资源访问。

判定结果只有 Allow / Deny；Ask 由本人确认或受控批准流程完成，本模块不产生 Ask。
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.utils import timezone as dj_timezone

from authorization.actions import CASE_ACTIONS, SERVICE_ACTIONS
from authorization.models import AccessGrant, ServiceRoleGrant
from family_content.models import EnvironmentGate
from family_content.gate_locks import read_environment_gate_shared
from identity.models import Principal
from identity.read_locks import read_principal_shared
from relations.models import Relation


class Denied(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str | None = None
    grant: AccessGrant | None = None
    service_grant: ServiceRoleGrant | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _active_at(grant, now) -> bool:
    if grant.revoked_at is not None and grant.revoked_at <= now:
        return False
    if grant.valid_until is not None and grant.valid_until <= now:
        return False
    return grant.valid_from <= now


def environment_enabled(environment: str) -> bool:
    gate = EnvironmentGate.objects.filter(environment=environment).first()
    if gate is None or not gate.enabled:
        raise Denied("ENVIRONMENT_DISABLED")
    return True


def check_principal_session(principal, session_auth_epoch) -> None:
    if principal.account_status != Principal.AccountStatus.ACTIVE:
        raise Denied("ACCOUNT_NOT_ACTIVE")
    if session_auth_epoch is None or session_auth_epoch != principal.auth_epoch:
        raise Denied("AUTH_EPOCH_MISMATCH")


def _relation_active_at(relation_id, patient_id, principal_id, now) -> bool:
    relation = (
        Relation.objects.filter(id=relation_id, patient_id=patient_id, guardian_id=principal_id)
        .first()
    )
    if relation is None or relation.status != Relation.Status.ACTIVE:
        return False
    return _active_at(relation, now)


def _scope_permits(grant, record_id, author_id) -> bool:
    if grant.record_scope == "all":
        return True
    if grant.record_scope == "own":
        return author_id is not None and author_id == grant.principal_id
    if grant.record_scope == "assigned":
        return record_id is not None and grant.assigned_record_id == record_id
    return False


def _field_codes_permitted(grant, requested_field_codes) -> bool:
    if not requested_field_codes:
        return True
    granted = set(grant.field_codes)
    return set(requested_field_codes) <= granted


def check_service_grant(principal, action, environment, now) -> ServiceRoleGrant:
    grant = (
        ServiceRoleGrant.objects.select_for_update().filter(
            principal_id=principal.id, environment=environment, action=action
        )
        .order_by("-valid_from", "-id")
        .first()
    )
    if grant is None or not _active_at(grant, now) or not grant.evidence_ref.strip():
        raise Denied("SERVICE_ACTION_DENIED")
    return grant


def matching_case_grants(
    principal,
    patient,
    action,
    now,
    *,
    field_codes=None,
    record_id=None,
    author_id=None,
    relation_id=None,
    require_relation=False,
    whole_resource=False,
    purpose=None,
):
    grants = AccessGrant.objects.filter(
        principal_id=principal.id, patient_id=patient.id, action=action
    ).order_by("-valid_from")
    if purpose is not None:
        grants = grants.filter(purpose=purpose)
    for grant in grants:
        if whole_resource and grant.field_codes:
            continue
        if not _active_at(grant, now):
            continue
        if not _scope_permits(grant, record_id, author_id):
            continue
        if not _field_codes_permitted(grant, field_codes):
            continue
        if require_relation or grant.relation_id is not None:
            if not _relation_active_at(grant.relation_id, patient.id, principal.id, now):
                continue
        yield grant


def check_case_grant(principal, patient, action, now, **scope) -> AccessGrant:
    for grant in matching_case_grants(principal, patient, action, now, **scope):
        return grant
    raise Denied("CASE_ACTION_DENIED")


@transaction.atomic
def evaluate(
    *,
    principal,
    patient,
    action,
    purpose,
    environment,
    session_auth_epoch,
    field_codes=None,
    record_id=None,
    author_id=None,
    require_relation=False,
    whole_resource=False,
    read_only=False,
    lock=True,
) -> PolicyDecision:
    """锁定当前环境和人员，岗位授权保持至调用事务结束。"""
    now = _utcnow()
    read_actions = {'read_raw', 'read_history', 'read_file', 'view_request', 'read_summary',
        'read_case', 'read_assessment', 'execute_assessment'}
    shared = read_only or action in read_actions
    if not lock:
        with connection.cursor() as cursor:
            cursor.execute('SHOW transaction_read_only')
            if cursor.fetchone()[0] != 'on':
                raise ImproperlyConfigured('无锁授权检查要求只读事务。')
        gate = EnvironmentGate.objects.filter(environment=environment).first()
    else:
        gate = (read_environment_gate_shared(environment) if shared or action in CASE_ACTIONS
            or action in {'manage_data_use', 'verify_field'}
            else EnvironmentGate.objects.select_for_update().filter(environment=environment).first())
    if gate is None or not gate.enabled:
        raise Denied('ENVIRONMENT_DISABLED')
    principal = (Principal.objects.get(id=principal.id) if not lock else
        read_principal_shared(principal.id) if shared else
        Principal.objects.select_for_update(no_key=True).get(id=principal.id))
    check_principal_session(principal, session_auth_epoch)
    if action in SERVICE_ACTIONS:
        grant = check_service_grant(principal, action, environment, now)
        return PolicyDecision(allowed=True, service_grant=grant)
    if action in CASE_ACTIONS:
        grant = check_case_grant(
            principal,
            patient,
            action,
            now,
            field_codes=field_codes,
            record_id=record_id,
            author_id=author_id,
            require_relation=require_relation,
            whole_resource=whole_resource,
            purpose=purpose,
        )
        return PolicyDecision(allowed=True, grant=grant)
    raise Denied("UNKNOWN_ACTION")
