from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from configurations.evidence import deployed_artifact_checksum, read_approved_evidence, validation_report
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.schemas import validate_operation_input, validate_payload
from configurations.service import ConfigRejected, compute_checksum, register_config
from family_content.models import EnvironmentGate
from files.purpose import lock_plan_scope, lock_processing_config
from files.purpose_dependencies import invalidate_resource
from identity.models import Principal
from sources.models import SourceRegistry, SourceRevision


CONFIG_OPERATIONS = {
    'config_create': ('manage_configs', 'ConfigInput', 'config'),
    'config_submit': ('manage_configs', 'StateAction', 'config'),
    'config_approve': ('approve_config', 'ApprovalInput', 'config_approval'),
    'config_approval_revoke': ('approve_config', 'StateAction', 'config_approval'),
    'config_validate': ('register_validation', 'ValidationInput', 'config_validation'),
    'config_enable': ('publish_config', 'GateAction', 'config'),
    'config_disable': ('publish_config', 'GateAction', 'config'),
}


def verify_content(config, environment):
    validate_payload(config.kind, config.schema_version, config.payload)
    if compute_checksum(config.payload) != config.checksum:
        raise ConfigRejected('CONFIG_CHECKSUM_MISMATCH')
    if 'environment' in config.payload and config.payload['environment'] != environment:
        raise ConfigRejected('CONFIG_ENVIRONMENT_MISMATCH')
    if config.source_revision_id is not None:
        revision = SourceRevision.objects.select_for_update().get(id=config.source_revision_id)
        source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
        if (revision.status != 'approved' or source.status != 'approved' or revision.captured_at > timezone.now()
                or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()):
            raise ConfigRejected('CONFIG_SOURCE_UNAVAILABLE')


def configuration_scope(config, gate, actor):
    if config.schema_version not in ('purpose-processing-2', 'purpose-processing-text-1'):
        return None
    plan, original, actual_gate, group = lock_plan_scope(config, actor, actor.auth_epoch)
    if actual_gate.environment != gate.environment:
        raise ConfigRejected('CONFIG_ENVIRONMENT_MISMATCH')
    return plan, original, group


def configuration_request_scope(actor, gate, operation, keys, payload):
    if operation == 'config_create':
        config = ConfigVersion(kind=payload['kind'], schema_version=payload['schema_version'], payload=payload['payload'],
            checksum=compute_checksum(payload['payload']), source_revision_id=payload['source_revision_id'])
    elif operation == 'config_approval_revoke':
        approval = ConfigApproval.objects.select_for_update().get(id=keys['approval_id'])
        config = ConfigVersion.objects.select_for_update().get(id=approval.config_id)
        if approval.effective_scope.get('environment') != gate.environment:
            raise ConfigRejected('CONFIG_ENVIRONMENT_MISMATCH')
    else:
        config = ConfigVersion.objects.select_for_update().get(id=keys['config_id'])
    if 'environment' in config.payload and config.payload['environment'] != gate.environment:
        raise ConfigRejected('CONFIG_ENVIRONMENT_MISMATCH')
    configuration_scope(config, gate, actor)


def configuration_target_ready(actor, gate, operation, keys, payload):
    if operation not in CONFIG_OPERATIONS:
        raise ConfigRejected('CONFIG_OPERATION_INVALID')
    validate_operation_input(CONFIG_OPERATIONS[operation][1], payload)
    if operation == 'config_create':
        validate_payload(payload['kind'], payload['schema_version'], payload['payload'])
        if not payload['config_key'].strip():
            return False
        provisional = ConfigVersion(kind=payload['kind'], schema_version=payload['schema_version'], payload=payload['payload'],
            checksum=compute_checksum(payload['payload']), source_revision_id=payload['source_revision_id'])
        verify_content(provisional, gate.environment)
        configuration_scope(provisional, gate, actor)
        return True
    approval = None
    if operation == 'config_approval_revoke':
        approval = ConfigApproval.objects.select_for_update().get(id=keys['approval_id'])
        config = ConfigVersion.objects.select_for_update().get(id=approval.config_id)
        if (approval.revoked_at is not None or payload['expected_state'] != 'active'
                or approval.effective_scope.get('environment') != gate.environment):
            return False
    else:
        config = ConfigVersion.objects.select_for_update().get(id=keys['config_id'])
    if 'environment' in config.payload and config.payload['environment'] != gate.environment:
        return False
    if 'expected_gate_version' in payload and payload['expected_gate_version'] != str(gate.version):
        return False
    if 'expected_version' in payload and payload['expected_version'] != str(config.version):
        return False
    if approval is None and 'expected_state' in payload and payload['expected_state'] != config.status:
        return False
    states = {'config_submit': ('draft',), 'config_approve': ('pending',),
        'config_validate': ('approved', 'enabled'), 'config_enable': ('approved',),
        'config_disable': ('enabled',)}
    if operation in states and config.status not in states[operation]:
        return False
    if operation not in ('config_disable', 'config_approval_revoke'):
        verify_content(config, gate.environment)
        purpose = configuration_scope(config, gate, actor)
        if purpose is not None and operation == 'config_approve':
            if payload['approval_role'] != 'data_processing' or payload['audience'] != ['support']:
                return False
        if purpose is not None and operation == 'config_validate':
            if payload['dataset_revision_id'] != str(purpose[2].source_revision_id):
                return False
    if 'config_checksum' in payload and payload['config_checksum'] != config.checksum:
        return False
    if 'environment' in payload and payload['environment'] != gate.environment:
        return False
    return True


def advance_configuration_gate(gate):
    gate.version += 1
    gate.publication_epoch += 1
    gate.updated_at = timezone.now()
    gate.save(update_fields=['version', 'publication_epoch', 'updated_at'])


def current_approval(config, gate):
    now = timezone.now()
    approvals = ConfigApproval.objects.select_for_update().filter(config=config, revoked_at__isnull=True,
        approved_at__lte=now, effective_scope__environment=gate.environment,
        effective_scope__config_checksum=config.checksum).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
    approval = approvals.order_by('-approved_at', '-id').first()
    if approval is None or not approval.approval_role.strip():
        raise ConfigRejected('CONFIG_APPROVAL_UNAVAILABLE')
    if Principal.objects.select_for_update().get(id=approval.approved_by_id).account_status != 'active':
        raise ConfigRejected('CONFIG_APPROVER_UNAVAILABLE')
    read_approved_evidence(approval.evidence_ref, purpose='configuration_approval')
    return approval


def current_validation(config, gate):
    validation = ConfigValidation.objects.select_for_update().filter(config=config, environment=gate,
        config_checksum=config.checksum, outcome='pass', revoked_at__isnull=True,
        executed_at__lte=timezone.now(), artifact_sha256=deployed_artifact_checksum(config)).order_by('-executed_at', '-id').first()
    if validation is None:
        raise ConfigRejected('CONFIG_VALIDATION_UNAVAILABLE')
    report, _ = validation_report(config, gate, {'config_checksum': config.checksum, 'environment': gate.environment,
        'artifact_sha256': validation.artifact_sha256, 'dataset_revision_id': str(validation.dataset_revision_id),
        'evidence_ref': validation.evidence_ref, 'evidence_sha256': validation.evidence_sha256})
    if (report.outcome != validation.outcome or report.executed_by != validation.executed_by_id
            or report.executed_at != validation.executed_at):
        raise ConfigRejected('CONFIG_VALIDATION_REPORT_MISMATCH')
    return validation


@transaction.atomic
def perform_configuration_operation(*, actor, gate, operation, keys, payload):
    gate = EnvironmentGate.objects.select_for_update().get(environment=gate.environment)
    if not configuration_target_ready(actor, gate, operation, keys, payload):
        raise ConfigRejected('CONFIG_TARGET_CHANGED')
    now = timezone.now()
    if operation == 'config_create':
        revision = SourceRevision.objects.get(id=payload['source_revision_id']) if payload['source_revision_id'] else None
        config = register_config(kind=payload['kind'], config_key=payload['config_key'], schema_version=payload['schema_version'],
            payload=payload['payload'], source_revision=revision, created_by=actor)
        return config, config
    approval = ConfigApproval.objects.select_for_update().get(id=keys['approval_id']) if operation == 'config_approval_revoke' else None
    config = ConfigVersion.objects.select_for_update().get(id=approval.config_id if approval else keys['config_id'])
    if operation == 'config_submit':
        config.status = 'pending'
        config.save(update_fields=['status'])
        return config, config
    if operation == 'config_approve':
        if not payload['approval_role'].strip() or not payload['audience'] or not set(payload['audience']) <= {'family', 'support'}:
            raise ConfigRejected('CONFIG_APPROVAL_SCOPE_INVALID')
        until = datetime.fromisoformat(payload['valid_until']) if payload['valid_until'] else None
        if until is not None and until <= now:
            raise ConfigRejected('CONFIG_APPROVAL_EXPIRED')
        read_approved_evidence(payload['evidence_ref'], purpose='configuration_approval')
        scope = {'environment': gate.environment, 'config_checksum': config.checksum, 'audience': payload['audience']}
        purpose = configuration_scope(config, gate, actor)
        if purpose is not None:
            plan, original, _ = purpose
            scope = {'patient_id': str(original.patient_id), 'original_file_id': str(original.id), 'purpose': plan.purpose,
                'target': plan.target, 'environment': gate.environment, 'config_checksum': config.checksum}
        approval = ConfigApproval.objects.create(config=config, approved_by=actor, approval_role=payload['approval_role'],
            approved_at=now, evidence_ref=payload['evidence_ref'], effective_scope=scope, valid_until=until)
        config.status = 'approved'
        config.save(update_fields=['status'])
        return approval, config
    if operation == 'config_validate':
        current_approval(config, gate)
        report, dataset = validation_report(config, gate, payload)
        validation = ConfigValidation.objects.create(config=config, config_checksum=config.checksum, environment=gate,
            artifact_sha256=report.artifact_sha256, dataset_revision=dataset, evidence_ref=payload['evidence_ref'],
            evidence_sha256=payload['evidence_sha256'], outcome=report.outcome,
            executed_by_id=report.executed_by, executed_at=report.executed_at)
        return validation, config
    if operation == 'config_enable':
        current_approval(config, gate)
        current_validation(config, gate)
        if config.schema_version == 'dynamic-schedule-2':
            from alerting.policies import DynamicSchedulePolicy
            from alerting.triggers import check_schedule_inputs
            import json

            schedule = DynamicSchedulePolicy.model_validate_json(json.dumps(config.payload))
            executor = Principal.objects.get(id=schedule.actor_id)
            check_schedule_inputs(config, schedule, executor, now.isoformat())
        config.status = 'enabled'
        config.save(update_fields=['status'])
        if config.kind in ('dictionary', 'mapping'):
            from dictionaries.service import materialize_configuration

            materialize_configuration(config)
        purpose = configuration_scope(config, gate, actor)
        if purpose is not None:
            plan, original, _ = purpose
            lock_processing_config(config.id, original, plan.purpose, plan.target, gate.environment)
        advance_configuration_gate(gate)
        from alerting.triggers import emit_configuration_change

        emit_configuration_change(config, gate, operation)
        return config, config
    if operation == 'config_disable':
        config.status = 'disabled'
        config.save(update_fields=['status'])
        invalidate_resource('config_version', config.id, str(config.version))
        advance_configuration_gate(gate)
        from alerting.triggers import emit_configuration_change

        emit_configuration_change(config, gate, operation)
        return config, config
    if operation == 'config_approval_revoke':
        approval.revoked_at, approval.revocation_reason = now, payload['reason']
        approval.save(update_fields=['revoked_at', 'revocation_reason'])
        invalidate_resource('config_approval', approval.id)
        advance_configuration_gate(gate)
        from alerting.triggers import emit_configuration_change

        emit_configuration_change(config, gate, operation)
        return approval, config
    raise ConfigRejected('CONFIG_OPERATION_INVALID')


def configuration_receipt_resource(saved, operation, keys, environment):
    if saved.resource_type == 'config':
        config = ConfigVersion.objects.filter(id=saved.resource_id).first()
    elif saved.resource_type == 'config_approval':
        approval = ConfigApproval.objects.select_related('config').filter(id=saved.resource_id).first()
        if approval is None or approval.effective_scope.get('environment') != environment:
            return False
        config = approval.config
        if operation == 'config_approval_revoke' and keys['approval_id'] != approval.id:
            return False
    else:
        validation = ConfigValidation.objects.select_related('config').filter(id=saved.resource_id, environment_id=environment).first()
        if validation is None:
            return False
        config = validation.config
    if config is None or ('config_id' in keys and keys['config_id'] != config.id):
        return False
    return 'environment' not in config.payload or config.payload['environment'] == environment
