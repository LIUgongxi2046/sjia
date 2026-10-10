from django.db import transaction
from django.conf import settings

from dependencies.models import DependencyEdge, DependencyNode
from files.service import FileRejected


def resource_node(resource_type, resource_id, resource_version, patient_id):
    node, created = DependencyNode.objects.get_or_create(resource_type=resource_type, resource_id=resource_id,
        resource_version=resource_version, defaults={'patient_id': patient_id, 'validity': 'valid'})
    node = DependencyNode.objects.select_for_update().get(id=node.id)
    if node.patient_id != patient_id or node.validity != 'valid':
        raise FileRejected('PURPOSE_DEPENDENCY_UNAVAILABLE')
    return node


def connect(dependent, prerequisite, edge_type):
    if dependent.id == prerequisite.id:
        raise FileRejected('PURPOSE_DEPENDENCY_SELF_REFERENCE')
    if prerequisite.patient_id is not None and prerequisite.patient_id != dependent.patient_id:
        raise FileRejected('PURPOSE_DEPENDENCY_PATIENT_MISMATCH')
    DependencyEdge.objects.get_or_create(dependent=dependent, prerequisite=prerequisite, edge_type=edge_type)


@transaction.atomic
def register_copy(copy, original, derived, authorization, config, approvals, validations, prepared=None):
    node = resource_node('purpose_copy', copy.id, str(copy.version), copy.patient_id)
    connect(node, resource_node('file_object', original.id, original.object_version, copy.patient_id), 'source')
    if copy.processing_evidence.get('file_page_id') is not None:
        connect(node, resource_node('file_page', copy.processing_evidence['file_page_id'], original.object_version, copy.patient_id), 'source')
    connect(node, resource_node('file_object', derived.id, derived.object_version, copy.patient_id), 'input')
    connect(node, resource_node('data_use_authorization', authorization.id, authorization.approved_at.isoformat(), copy.patient_id), 'policy')
    connect(node, resource_node('source_revision', authorization.source_revision_id, authorization.source_revision.version, None), 'source')
    connect(node, resource_node('retention_policy', original.retention_policy_id, str(original.retention_policy.version), None), 'policy')
    connect(node, resource_node('config_version', config.id, str(config.version), None), 'mapping')
    for approval in approvals:
        connect(node, resource_node('config_approval', approval.id, approval.approved_at.isoformat(), None), 'policy')
    for validation in validations:
        connect(node, resource_node('config_validation', validation.id, validation.executed_at.isoformat(), None), 'verification')
    if prepared is not None:
        connect(node, resource_node('file_object', prepared.id, prepared.object_version, copy.patient_id), 'source')
    return node


@transaction.atomic
def register_review(copy, review, service_grants, case_grants):
    node = resource_node('purpose_copy_review', review.id, str(review.copy_version), copy.patient_id)
    connect(node, resource_node('purpose_copy', copy.id, str(copy.version), copy.patient_id), 'source')
    for grant in service_grants:
        connect(node, resource_node('service_role_grant', grant.id, grant.valid_from.isoformat(), None), 'policy')
    for grant in case_grants:
        connect(node, resource_node('access_grant', grant.id, grant.valid_from.isoformat(), copy.patient_id), 'policy')
    return node


def current_copy_dependencies(copy, review=None):
    node = DependencyNode.objects.select_for_update().filter(resource_type='purpose_copy', resource_id=copy.id,
        resource_version=str(copy.version), patient_id=copy.patient_id, validity='valid').first()
    if node is None:
        return False
    if DependencyEdge.objects.filter(dependent=node, prerequisite__validity='invalid').exists():
        return False
    if review is not None:
        review_node = DependencyNode.objects.select_for_update().filter(resource_type='purpose_copy_review', resource_id=review.id,
            resource_version=str(review.copy_version), patient_id=copy.patient_id, validity='valid').first()
        if review_node is None or DependencyEdge.objects.filter(dependent=review_node, prerequisite__validity='invalid').exists():
            return False
    return True


@transaction.atomic
def invalidate_resource(resource_type, resource_id, resource_version=None):
    query = DependencyNode.objects.select_for_update().filter(resource_type=resource_type, resource_id=resource_id)
    if resource_version is not None:
        query = query.filter(resource_version=resource_version)
    pending = {node.id for node in query}
    changed = set()
    while pending:
        current = pending - changed
        if not current:
            break
        locked = list(DependencyNode.objects.select_for_update().filter(id__in=current).order_by('id'))
        DependencyNode.objects.filter(id__in=[node.id for node in locked]).update(validity='invalid')
        changed.update(current)
        pending = set(DependencyEdge.objects.filter(prerequisite_id__in=current).values_list('dependent_id', flat=True))
    if changed and resource_type not in ('verification', 'config_version', 'config_approval'):
        from identity.models import Patient
        from tasks.outbox import emit_event

        patient_ids = DependencyNode.objects.filter(id__in=changed, patient_id__isnull=False).values_list('patient_id', flat=True).distinct()
        for patient in Patient.objects.filter(id__in=patient_ids).order_by('id'):
            emit_event(event_type='clinical.context_changed', schema_version='clinical-change-2',
                aggregate_id=patient.id, aggregate_version=patient.data_epoch, correlation_id=resource_id,
                resource_refs={'environment': settings.SJIA_ENVIRONMENT, 'patient_id': str(patient.id),
                    'resource_type': resource_type, 'resource_id': str(resource_id), 'resource_version': resource_version,
                    'invalidated_node_ids': sorted(str(value) for value in DependencyNode.objects.filter(
                        id__in=changed, patient_id=patient.id).values_list('id', flat=True))})
    return sorted(str(value) for value in changed)
