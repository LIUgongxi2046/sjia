from django.conf import settings
from django.db import transaction

from archives.models import PurposeCopy
from case_details.evaluation_units import EvaluationUnitsRejected, validate_unit_sources
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from files.models import FilePage
from files.purpose import checked_bytes, current_review, lock_copy_context
from identity.models import Patient


def required_file_bindings(inventory, manifest):
    validate_unit_sources(inventory, manifest)
    if manifest.files is None or manifest.files.state != 'complete' or not manifest.files.items:
        raise EvaluationUnitsRejected('UNIT_FILE_BINDINGS_REQUIRED')
    return {item.source_id: item for item in manifest.files.items}


@transaction.atomic
def lock_evaluation_file_bindings(*, inventory, manifest, actor, auth_epoch, purpose, target, environment, action='manage_data_use'):
    bindings = required_file_bindings(inventory, manifest)
    if (environment != settings.SJIA_ENVIRONMENT or not isinstance(purpose, str) or not purpose.strip()
            or not isinstance(target, str) or not target.strip()):
        raise EvaluationUnitsRejected('UNIT_PURPOSE_SCOPE_INVALID')
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    if not gate.enabled:
        raise EvaluationUnitsRejected('UNIT_ENVIRONMENT_UNAVAILABLE')
    copy_ids = {item.purpose_copy_id for item in bindings.values()}
    copies = list(PurposeCopy.objects.filter(id__in=copy_ids).order_by('id'))
    if len(copies) != len(copy_ids):
        raise EvaluationUnitsRejected('UNIT_PURPOSE_COPY_UNAVAILABLE')
    # 多患儿集合按固定顺序取得患儿记录，后续用途服务继续核查当前许可。
    patient_ids = sorted({item.patient_id for item in copies})
    list(Patient.objects.select_for_update().filter(id__in=patient_ids).order_by('id'))
    sources = {item['source_id']: item for item in inventory['files']}
    proofs, files = [], {}
    for source_id, binding in sorted(bindings.items()):
        copy, original, derived, current_actor, patient, current_gate, group = lock_copy_context(
            binding.purpose_copy_id, actor, auth_epoch, service=True, service_action=action)
        source = sources[source_id]
        if (copy.patient_id != binding.patient_id or original.id != binding.id
                or original.patient_id != binding.patient_id or original.object_version != binding.object_version
                or original.file_group_id != binding.file_group_id or group.source_revision_id != binding.source_revision_id
                or original.checksum != source['sha256'] or original.size_bytes != source['size_bytes']
                or copy.version != int(binding.purpose_copy_version) or derived.id != binding.copy_file_id
                or derived.object_version != binding.copy_object_version or derived.checksum != binding.copy_sha256
                or copy.purpose != purpose or copy.processing_evidence['target'] != target
                or copy.environment != environment or current_gate.environment != environment):
            raise EvaluationUnitsRejected('UNIT_FILE_BINDING_CHANGED')
        review = current_review(copy, original, derived)
        if review is None:
            raise EvaluationUnitsRejected('UNIT_PURPOSE_REVIEW_UNAVAILABLE')
        checked_bytes(original)
        checked_bytes(derived)
        files[original.id] = original
        proofs.append({'source_id': source_id, 'file_id': str(original.id), 'patient_id': str(patient.id),
            'file_group_id': str(group.id), 'file_group_version': str(group.version),
            'source_revision_id': str(group.source_revision_id), 'object_version': original.object_version,
            'checksum': original.checksum, 'copy_id': str(copy.id), 'copy_version': str(copy.version),
            'copy_file_id': str(derived.id), 'copy_object_version': derived.object_version, 'copy_checksum': derived.checksum,
            'review_id': str(review.id), 'reviewed_at': review.reviewed_at.isoformat(),
            'use_authorization_id': str(copy.use_authorization_id), 'actor_id': str(current_actor.id),
            'auth_epoch': str(current_actor.auth_epoch), 'data_epoch': str(patient.data_epoch),
            'verification_epoch': str(patient.verification_epoch)})
    pages = []
    for item in sorted(manifest.entries('pages'), key=lambda row: row.id):
        original = files[item.file_id]
        page = FilePage.objects.select_for_update().filter(id=item.id, patient_id=original.patient_id).first()
        if (page is None or page.file_id != original.id or page.file_group_id != item.file_group_id
                or page.ordinal != item.ordinal):
            raise EvaluationUnitsRejected('UNIT_PAGE_BINDING_CHANGED')
        pages.append({'page_id': str(page.id), 'patient_id': str(page.patient_id),
            'file_id': str(page.file_id), 'file_group_id': str(page.file_group_id), 'ordinal': page.ordinal})
    return {'environment': environment, 'purpose': purpose, 'target': target, 'gate_version': str(gate.version),
        'source_manifest_sha256': inventory['source_manifest_sha256'],
        'unit_manifest_content_sha256': compute_checksum(manifest.model_dump(mode='json')),
        'file_bindings': proofs, 'page_bindings': pages, 'clinical_qualification': 'not_evaluated'}
