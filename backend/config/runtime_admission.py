"""Process-local runtime admission; no provider call and no approval fabrication.

Management commands never call this module implicitly. Actual production serving,
worker and scheduler entrypoints do, after migrations can be run independently.
"""
from copy import deepcopy
import os
from pathlib import Path
import re
from threading import RLock
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from configurations.evidence import artifact_verification_scope

_LOCK = RLock()
_ADMITTED = None


class RuntimeAdmissionRejected(RuntimeError):
    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _reject(code):
    raise RuntimeAdmissionRejected(code)


def _restore_admission():
    # Recovery safety is mandatory when a restore marker exists, independently
    # of general runtime qualification and live-disposition feature enablement.
    from retention.content_recovery import assert_configured_restore_admission
    try:
        return assert_configured_restore_admission()
    except Exception as exc:
        _reject(getattr(exc, 'reason_code', 'RESTORE_RUNTIME_NOT_ADMITTED'))


def desired_identity(gate):
    return {'environment': gate.environment, 'gate_version': gate.version,
        'publication_epoch': gate.publication_epoch,
        'runtime_config_id': str(gate.runtime_config_id) if gate.runtime_config_id else None}


def process_snapshot():
    with _LOCK:
        # A preloaded parent snapshot is not evidence that a forked child admitted
        # its own dependencies. Gunicorn is deployed without --preload.
        return deepcopy(_ADMITTED) if _ADMITTED and _ADMITTED['pid'] == os.getpid() else None


def _local_directory(path, *, writable=False):
    if not path:
        return False
    value = Path(path)
    return value.is_dir() and not value.is_symlink() and os.access(value,
        os.R_OK | os.X_OK | (os.W_OK if writable else 0))


def _secret_available(reference):
    return bool(isinstance(reference, str) and re.fullmatch(r'env:[A-Z][A-Z0-9_]*', reference)
        and os.environ.get(reference[4:]))


def _platform_reason(payload):
    api = payload['api']
    if not api['lock_timeout_ms'] < api['statement_timeout_ms'] < api['request_timeout_ms']:
        return 'RUNTIME_TIMEOUT_ORDER_INVALID'
    if api['upload_timeout_ms'] < api['request_timeout_ms']:
        return 'RUNTIME_UPLOAD_TIMEOUT_INVALID'
    if getattr(settings, 'REQUIRE_PROCESS_RUNTIME_ADMISSION', False):
        session = payload['session']
        expected = {'SESSION_COOKIE_SECURE': session['secure'], 'CSRF_COOKIE_SECURE': True,
            'SESSION_COOKIE_HTTPONLY': session['http_only'], 'CSRF_USE_SESSIONS': session['csrf_use_sessions'],
            'SESSION_COOKIE_SAMESITE': session['same_site'], 'SESSION_COOKIE_NAME': session['cookie_name'],
            'CSRF_HEADER_NAME': 'HTTP_X_CSRFTOKEN'}
        if settings.DEBUG or any(getattr(settings, key, None) != value for key, value in expected.items()):
            return 'RUNTIME_SESSION_CONFIGURATION_MISMATCH'
        if not settings.SECRET_KEY or len(settings.SECRET_KEY) < 32 or '*' in settings.ALLOWED_HOSTS:
            return 'RUNTIME_PROCESS_IDENTITY_INVALID'
    if not _local_directory(settings.CONFIG_EVIDENCE_ROOT):
        return 'RUNTIME_EVIDENCE_STORE_UNAVAILABLE'
    if not settings.REDIS_URL or settings.CELERY_BROKER_URL != settings.REDIS_URL:
        return 'RUNTIME_BROKER_CONFIGURATION_MISMATCH'
    return None


def _provider_reason(entry, provider, environment, task_types):
    from configurations.qualification import configuration_qualification_reason
    from configurations.models import ConfigVersion
    kind = entry['kind']
    if provider is None or provider.kind != 'provider':
        return 'RUNTIME_PROVIDER_CONFIGURATION_UNAVAILABLE'
    reason = configuration_qualification_reason(provider, environment,
        audience='family' if kind in ('ocr', 'voice') else 'support')
    if reason:
        return reason
    if kind in ('ocr', 'voice'):
        if provider.schema_version != 'provider-1' or 'ocr' not in task_types:
            return 'RUNTIME_PROVIDER_PROTOCOL_UNAVAILABLE'
        endpoint = urlsplit(entry['endpoint'])
        if (endpoint.scheme != 'https' or not endpoint.netloc or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment
                or entry['endpoint'].rstrip('/') != provider.payload['endpoint'].rstrip('/')
                or entry['endpoint'].rstrip('/') != settings.ARK_BASE_URL.rstrip('/')):
            return 'RUNTIME_PROVIDER_ENDPOINT_MISMATCH'
        if (entry['secret_ref'] != 'env:SJIA_ARK_API_KEY' or not settings.ARK_API_KEY
                or os.environ.get('SJIA_ARK_API_KEY') != settings.ARK_API_KEY):
            return 'RUNTIME_PROVIDER_SECRET_UNAVAILABLE'
        ids = {provider.payload['field_config_id'], *provider.payload.get('workflow_field_config_ids', {}).values()}
        for identity in ids:
            field = ConfigVersion.objects.filter(id=identity, kind='content', schema_version='field-config-1').first()
            if field is None or configuration_qualification_reason(field, environment):
                return 'RUNTIME_FIELD_CONFIGURATION_UNAVAILABLE'
        if entry['currency'] != provider.payload['currency']:
            return 'RUNTIME_PROVIDER_BUDGET_MISMATCH'
        from decimal import Decimal, InvalidOperation
        try:
            amounts = (Decimal(entry.get('daily_budget')), Decimal(entry.get('reserved_cost')))
        except (TypeError, ValueError, InvalidOperation):
            return 'RUNTIME_PROVIDER_BUDGET_INVALID'
        if (not isinstance(entry.get('billing_scope_key'), str) or not entry['billing_scope_key'].strip()
                or any(not amount.is_finite() or amount <= 0 for amount in amounts)):
            return 'RUNTIME_PROVIDER_BUDGET_INVALID'
    elif kind == 'notification':
        if provider.schema_version != 'notification-in-app-2' or 'notification' not in task_types:
            return 'RUNTIME_PROVIDER_PROTOCOL_UNAVAILABLE'
        from notifications.native import native_configuration
        from family_content.models import EnvironmentGate
        try:
            native_configuration(EnvironmentGate.objects.get(environment=environment), lock=False)
        except Exception as exc:
            return getattr(exc, 'reason_code', 'RUNTIME_NOTIFICATION_CONFIGURATION_UNAVAILABLE')
    else:
        # No connector for these provider kinds is implemented/registered in
        # current source. An enabled row cannot become a fictitious capability.
        return 'RUNTIME_PROVIDER_PROTOCOL_UNAVAILABLE'
    if not _secret_available(entry['secret_ref']):
        return 'RUNTIME_PROVIDER_SECRET_UNAVAILABLE'
    return None


def _merge_provider_capability(capabilities, kind, *, enabled, reason=None):
    """Aggregate declarations monotonically: any enabled refusal wins.

    Domain validation currently rejects repeated kinds. This defensive reducer
    cannot hide a refused enabled provider if that contract later allows them.
    """
    previous = capabilities.get(kind)
    if not enabled:
        capabilities.setdefault(kind, {'state':'disabled','reason_code':'CAPABILITY_NOT_ENABLED'})
    elif previous is None or previous['state'] != 'blocked':
        capabilities[kind] = {'state':'blocked' if reason else 'configured',
            'reason_code':reason,'requires_request_authorization':True,'required_for_startup':True}


def _capability_policies(task_types, environment):
    from configurations.models import ConfigVersion
    from configurations.qualification import configuration_qualification_reason
    needed = {'assessment': ('assessment-rule-2','assessment-context-policy-2','qualification-policy-2','unit-mapping-2'),
        'alert_evaluation': ('dynamic-input-policy-2','alert-lifecycle-2','dynamic-schedule-2')}
    identities, reasons = [], {}
    for capability, schemas in needed.items():
        if capability not in task_types:
            continue
        for schema in schemas:
            qualified = [row for row in ConfigVersion.objects.filter(schema_version=schema, status='enabled')
                if configuration_qualification_reason(row, environment, audience='support') is None]
            if not qualified:
                reasons[capability] = 'RUNTIME_CAPABILITY_POLICY_UNAVAILABLE'
            identities.extend({'id':str(row.id),'checksum':row.checksum} for row in qualified)
    if 'summary' in task_types:
        from summaries.service import SummaryRejected, current_summary_config, summary_publication_gate
        try:
            template = current_summary_config('summary', environment, lock=False)
            visibility = current_summary_config('visibility', environment, lock=False)
            if summary_publication_gate(environment, template, visibility) is None:
                reasons['summary'] = 'RUNTIME_SUMMARY_PUBLICATION_UNAVAILABLE'
            identities.extend({'id':str(row.id),'checksum':row.checksum} for row in (template,visibility))
        except SummaryRejected as exc:
            reasons['summary'] = exc.reason_code
    return identities, reasons


@transaction.atomic
@artifact_verification_scope()
def inspect_runtime(environment=None):
    """Read current qualified desired state; never update the admitted snapshot."""
    _restore_admission()
    from configurations.models import ConfigVersion
    from configurations.qualification import configuration_qualification_reason
    from configurations.service import compute_checksum
    from configurations.schemas import payload_reason
    from configurations.evidence import inspect_approved_evidence
    from family_content.models import EnvironmentGate
    from retention.models import RetentionPolicy
    from sources.operations import RetentionRules

    environment = environment or settings.SJIA_ENVIRONMENT
    gate = EnvironmentGate.objects.filter(environment=environment).first()
    if gate is None or not gate.enabled or gate.runtime_config_id is None:
        _reject('RUNTIME_CONFIGURATION_REQUIRED')
    runtime = ConfigVersion.objects.filter(id=gate.runtime_config_id, kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None:
        _reject('RUNTIME_CONFIGURATION_REQUIRED')
    reason = payload_reason(runtime.kind, runtime.schema_version, runtime.payload)
    if reason:
        _reject(reason)
    reason = configuration_qualification_reason(runtime, environment, audience='support')
    if reason:
        _reject(reason)
    payload = runtime.payload
    reason = _platform_reason(payload)
    if reason:
        _reject(reason)
    task_types = {row['task_type'] for row in payload['tasks']}
    if any(row['enabled'] and row['kind'] in ('ocr','voice') for row in payload['providers']):
        reason = configuration_qualification_reason(runtime, environment, audience='family')
        if reason:
            _reject(reason)
    policies = list(RetentionPolicy.objects.select_related('approved_by').filter(id__in=payload['retention_policy_ids']))
    now = timezone.now()
    if len(policies) != len(payload['retention_policy_ids']):
        _reject('RUNTIME_RETENTION_UNAVAILABLE')
    for policy in policies:
        if (policy.status != 'active' or policy.approved_at is None or policy.approved_at > now
                or policy.approved_by_id is None or policy.approved_by.account_status != 'active'
                or policy.schema_version != 'retention-1'):
            _reject('RUNTIME_RETENTION_UNAVAILABLE')
        try:
            rules = RetentionRules.model_validate(policy.rules)
            if rules.disposition == 'delete' and rules.retention_days is None:
                _reject('RUNTIME_RETENTION_UNAVAILABLE')
        except (TypeError, ValueError):
            _reject('RUNTIME_RETENTION_UNAVAILABLE')
        _, _, reason = inspect_approved_evidence(policy.evidence_ref, purpose='configuration_approval')
        if reason:
            _reject('RUNTIME_RETENTION_EVIDENCE_UNAVAILABLE')
    classes = [row.data_class for row in policies]
    if classes.count('audit') != 1 or classes.count('request') != 1:
        _reject('RUNTIME_CORE_RETENTION_REQUIRED')
    providers, capabilities = [], {}
    for entry in payload['providers']:
        kind = entry['kind']
        if not entry['enabled']:
            _merge_provider_capability(capabilities, kind, enabled=False)
            continue
        provider = ConfigVersion.objects.filter(id=entry['config_id']).first()
        reason = _provider_reason(entry, provider, environment, task_types)
        _merge_provider_capability(capabilities, kind, enabled=True, reason=reason)
        if provider:
            providers.append({'id':str(provider.id),'checksum':provider.checksum})
    policy_ids, reasons = _capability_policies(task_types, environment)
    for task in task_types - {'ocr','notification'}:
        reason = reasons.get(task)
        if task == 'file_validation' and (not _local_directory(settings.FILE_OBJECT_ROOT, writable=True) or 'file' not in classes):
            reason = 'RUNTIME_FILE_STORE_UNAVAILABLE'
        capabilities[task] = {'state':'not_configured' if reason else
            ('configured' if task in ('summary','assessment','alert_evaluation') else 'budget_configured'),
            'reason_code':reason,'requires_request_authorization':True,'required_for_startup':False}
    for kind in ('ocr','voice','notification'):
        capabilities.setdefault(kind, {'state':'disabled','reason_code':'CAPABILITY_NOT_ENABLED'})
    if any(entry['enabled'] and entry['kind'] in ('ocr','voice') for entry in payload['providers']) and not _local_directory(settings.FILE_OBJECT_ROOT, writable=True):
        _reject('RUNTIME_FILE_STORE_UNAVAILABLE')
    identity = {**desired_identity(gate), 'runtime_checksum':runtime.checksum,
        'runtime_version':str(runtime.version),
        'dependency_checksum':compute_checksum({'providers':sorted(providers,key=lambda x:x['id']),
            'policies':sorted(policy_ids,key=lambda x:x['id']),
            'retention':[{'id':str(row.id),'version':str(row.version),'rules':row.rules}
                for row in sorted(policies,key=lambda row:str(row.id))]})}
    return {'desired':identity,'capabilities':capabilities,'runtime_payload':deepcopy(payload)}


def admission_status(environment=None):
    loaded = process_snapshot()
    result = {'loaded_config_version':loaded,'desired_config_version':None,'capabilities':{},'reason_code':None}
    try:
        current = inspect_runtime(environment)
        result.update(desired_config_version=current['desired'], capabilities=current['capabilities'])
        blocked = [value['reason_code'] for value in current['capabilities'].values() if value['state']=='blocked' and value.get('required_for_startup')]
        if blocked:
            result['reason_code'] = blocked[0]
        elif loaded is None:
            result['reason_code'] = 'PROCESS_RUNTIME_NOT_ADMITTED'
        elif loaded['identity'] != current['desired']:
            result['reason_code'] = 'PROCESS_RUNTIME_CONFIGURATION_DRIFT'
    except RuntimeAdmissionRejected as exc:
        result['reason_code'] = exc.reason_code
    except Exception:
        result['reason_code'] = 'RUNTIME_CONFIGURATION_UNAVAILABLE'
    result['state'] = 'admitted_current' if result['reason_code'] is None else 'not_ready'
    return result


def admit_process_runtime(process_kind):
    global _ADMITTED
    if process_kind not in ('api','worker','beat'):
        _reject('PROCESS_KIND_INVALID')
    from observability.health import check_database, check_schema, check_broker
    if not check_database() or not check_schema() or not check_broker():
        _reject('RUNTIME_DEPENDENCY_UNAVAILABLE')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_user')
        actual = cursor.fetchone()
    expected = settings.DATABASES['default']
    if actual != (expected['NAME'],expected['USER']):
        _reject('RUNTIME_DATABASE_IDENTITY_MISMATCH')
    current = inspect_runtime()
    if any(value['state']=='blocked' and value.get('required_for_startup') for value in current['capabilities'].values()):
        _reject('RUNTIME_ENABLED_CAPABILITY_UNAVAILABLE')
    candidate = {**current['desired'], 'pid':os.getpid(),'process_kind':process_kind,'admitted_at':timezone.now().isoformat(),
        'identity':current['desired'],'capabilities':current['capabilities']}
    with _LOCK:
        if _ADMITTED is not None and _ADMITTED['pid']==os.getpid():
            if _ADMITTED['identity']!=candidate['identity']:
                _reject('PROCESS_RUNTIME_RESTART_REQUIRED')
            return deepcopy(_ADMITTED)
        _ADMITTED = deepcopy(candidate)
    return process_snapshot()


def admit_configured_process(process_kind):
    _restore_admission()
    if getattr(settings,'REQUIRE_PROCESS_RUNTIME_ADMISSION',False):
        try:
            return admit_process_runtime(process_kind)
        except RuntimeAdmissionRejected as exc:
            raise RuntimeAdmissionRejected(exc.reason_code) from None
        except Exception:
            raise RuntimeAdmissionRejected('RUNTIME_PROCESS_ADMISSION_FAILED') from None
    return None


def celery_process_start(sender=None, **kwargs):
    import django
    django.setup()
    try:
        admit_configured_process('worker')
    except Exception:
        # Celery signals log/swallow Exception; SystemExit makes failed admission
        # actually stop the worker rather than silently start with missing config.
        raise SystemExit('RUNTIME_PROCESS_ADMISSION_FAILED') from None


def celery_beat_start(sender=None, **kwargs):
    import django
    django.setup()
    try:
        admit_configured_process('beat')
    except Exception:
        raise SystemExit('RUNTIME_PROCESS_ADMISSION_FAILED') from None
