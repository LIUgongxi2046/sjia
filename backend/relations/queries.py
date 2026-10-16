from relations.models import RelationApplication
from relations.scope import scope_actions, scope_fingerprint, scope_valid_until, scoped_relations


def relation_actions(relation, principal):
    return relation_view(relation, principal)['actions']


def relation_view(relation, principal, *, detail=False):
    if relation.guardian_id != principal.id:
        raise ValueError('关联所属人员与当前读取人员不一致。')
    # Never mix a previously fetched relation/patient with newer grant rows.
    current = scoped_relations(principal).get(id=relation.id)
    return _relation_view(current, detail=detail)


def _relation_view(relation, *, detail=False):
    scope = relation._authorization_scope
    value = {
        'relation_id': str(relation.id), 'patient_id': str(relation.patient_id), 'display_id': relation.patient.display_id,
        'status': relation.status, 'version': str(relation.version), 'valid_from': relation.valid_from.isoformat(),
        'valid_until': relation.valid_until.isoformat() if relation.valid_until else None,
        'actions': scope_actions(scope),
        'authorization_scope_fingerprint': scope_fingerprint(scope),
        'authorization_scope_valid_until': scope_valid_until(scope, relation._authorization_scope_at),
    }
    if detail:
        value['auth_epoch'] = str(relation.auth_epoch)
    return value


def relation_list_read(principal):
    relations = scoped_relations(principal).order_by('-valid_from', '-id')
    return {'relations': [_relation_view(relation) for relation in relations]}


def application_view(application, principal, *, detail=False):
    if application.applicant_id != principal.id:
        raise ValueError('申请所属人员与当前读取人员不一致。')
    value = {'application_id': str(application.id), 'version': str(application.version), 'status': application.status,
        'patient_id': str(application.patient_id) if application.patient_id else None, 'reason': application.reason,
        'note': application.note, 'requested_access': application.requested_access,
        **application_declaration_view(application)}
    if detail:
        value.update(evidence_ref=application.evidence_ref, created_at=application.created_at.isoformat())
    return value


def application_declaration_view(application):
    # Even an approved application does not verify the applicant's chosen label.
    return {'applicant_declaration': application.applicant_declaration,
        'declaration_verification': 'unverified' if application.applicant_declaration is not None else 'not_provided'}


def application_list_read(principal):
    applications = RelationApplication.objects.filter(applicant=principal).order_by('-created_at', '-id')
    return {'applications': [application_view(application, principal) for application in applications]}
