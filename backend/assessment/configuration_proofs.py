from django.db.models import Q
from django.utils import timezone

from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected
from configurations.evidence import deployed_artifact_checksum
from configurations.models import ConfigApproval, ConfigValidation
from configurations.qualification import configuration_qualification_reason


def configuration_proof(config, environment, known_at, mode, *, artifact_sha256=None):
    now = timezone.now()
    artifact = deployed_artifact_checksum(config) if artifact_sha256 is None else artifact_sha256
    approval = ConfigApproval.objects.select_for_update().filter(config=config, revoked_at__isnull=True,
        approved_at__lte=known_at, effective_scope__environment=environment,
        effective_scope__config_checksum=config.checksum, effective_scope__audience__contains=['support']).filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=now)).order_by('-approved_at', '-id').first()
    validation = ConfigValidation.objects.select_for_update().filter(config=config, environment_id=environment,
        config_checksum=config.checksum, artifact_sha256=artifact, executed_at__lte=known_at,
        outcome='pass', revoked_at__isnull=True).order_by('-executed_at', '-id').first()
    if approval is None or validation is None:
        raise QualificationRejected('ASSESSMENT_CONFIGURATION_PROOF_REQUIRED')
    for point in (known_at, now):
        reason = configuration_qualification_reason(config, environment, now=point, audience='support',
            allow_historical=mode == 'replay', approval_id=approval.id, validation_id=validation.id, artifact_sha256=artifact)
        if reason:
            raise QualificationRejected(reason)
    return {'config': entity_reference(config), 'approval': entity_reference(approval),
        'validation': entity_reference(validation), 'artifact_sha256': artifact}
