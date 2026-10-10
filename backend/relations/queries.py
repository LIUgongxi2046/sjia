from django.db.models import Q
from django.utils import timezone

from authorization.models import AccessGrant
from relations.models import Relation, RelationApplication


def relation_actions(relation, principal):
    now = timezone.now()
    if relation.guardian_id != principal.id:
        raise ValueError('关联所属人员与当前读取人员不一致。')
    if relation.status != 'active' or relation.valid_from > now or (relation.valid_until and relation.valid_until <= now):
        return []
    return list(AccessGrant.objects.filter(relation_id=relation.id, principal=principal, patient=relation.patient,
        valid_from__lte=now, revoked_at__isnull=True).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .values_list('action', flat=True).distinct())


def relation_view(relation, principal, *, detail=False):
    value = {
        'relation_id': str(relation.id), 'patient_id': str(relation.patient_id), 'display_id': relation.patient.display_id,
        'status': relation.status, 'version': str(relation.version), 'valid_from': relation.valid_from.isoformat(),
        'valid_until': relation.valid_until.isoformat() if relation.valid_until else None,
        'actions': relation_actions(relation, principal),
    }
    if detail:
        value['auth_epoch'] = str(relation.auth_epoch)
    return value


def relation_list_read(principal):
    relations = Relation.objects.filter(guardian=principal).select_related('patient').order_by('-valid_from', '-id')
    return {'relations': [relation_view(relation, principal) for relation in relations]}


def application_view(application, principal, *, detail=False):
    if application.applicant_id != principal.id:
        raise ValueError('申请所属人员与当前读取人员不一致。')
    value = {'application_id': str(application.id), 'version': str(application.version), 'status': application.status,
        'patient_id': str(application.patient_id) if application.patient_id else None, 'reason': application.reason,
        'note': application.note, 'requested_access': application.requested_access}
    if detail:
        value.update(evidence_ref=application.evidence_ref, created_at=application.created_at.isoformat())
    return value


def application_list_read(principal):
    applications = RelationApplication.objects.filter(applicant=principal).order_by('-created_at', '-id')
    return {'applications': [application_view(application, principal) for application in applications]}
