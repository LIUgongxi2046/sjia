"""Read-only permission revision markers, never authorization credentials.

The whole projection is one SQL statement, so actions and the marker cannot
observe different grant revisions. No new locks, transaction isolation changes,
or clinical reads are introduced into the existing audited HTTP boundary.
"""
from hashlib import sha256
import json

from django.conf import settings
from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import BooleanField, Case, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import JSONObject
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from authorization.models import AccessGrant
from family_content.models import EnvironmentGate
from relations.models import Relation


def _active_condition(now):
    # Same validity/revocation boundaries as authorization.policy._active_at.
    return (Q(valid_from__lte=now)
        & (Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        & (Q(revoked_at__isnull=True) | Q(revoked_at__gt=now)))


def _active_value(now, *, relation=False):
    condition = _active_condition(now)
    if relation:
        condition &= Q(status=Relation.Status.ACTIVE)
    return Case(When(condition, then=Value(True)), default=Value(False), output_field=BooleanField())


def _relation_value(now):
    return JSONObject(id='id', patient_id='patient_id', guardian_id='guardian_id',
        version='version', auth_epoch='auth_epoch', status='status',
        valid_from='valid_from', valid_until='valid_until', revoked_at='revoked_at',
        active=_active_value(now, relation=True))


def scoped_relations(principal):
    """One MVCC statement for the caller's relations and all permission inputs.

    Some case reads allow relationless grants; policy may also use a different
    relation's grant. Include every actor/patient grant conservatively, including
    its purpose and record/field restrictions and the referenced relation state.
    Inactive rows are included too, so replacement/revocation never looks like an
    unchanged scope merely because another grant supplies the same action.
    """
    now = timezone.now()
    linked_relation = Relation.objects.filter(id=OuterRef('relation_id'),
        patient_id=OuterRef('patient_id'), guardian_id=OuterRef('principal_id'))
    grants = AccessGrant.objects.filter(principal_id=OuterRef('guardian_id'),
        patient_id=OuterRef('patient_id')).order_by('id').annotate(scope_value=JSONObject(
            id='id', principal_id='principal_id', patient_id='patient_id', relation_id='relation_id',
            action='action', field_codes='field_codes', record_scope='record_scope',
            assigned_record_id='assigned_record_id', purpose='purpose', valid_from='valid_from',
            valid_until='valid_until', revoked_at='revoked_at', active=_active_value(now),
            relation=Subquery(linked_relation.annotate(scope_value=_relation_value(now))
                .values('scope_value')[:1]),
        )).values('scope_value')
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).annotate(
        scope_value=JSONObject(environment='environment', enabled='enabled', version='version'))
    return Relation.objects.filter(guardian_id=principal.id).select_related('patient').annotate(
        _authorization_scope_at=Value(now),
        _authorization_scope=JSONObject(
            schema=Value('relation-authorization-scope-1'),
            principal=JSONObject(id='guardian_id', account_status='guardian__account_status',
                auth_epoch='guardian__auth_epoch'),
            patient=JSONObject(id='patient_id', status='patient__status'),
            relation=_relation_value(now),
            environment=JSONObject(name=Value(settings.SJIA_ENVIRONMENT),
                gate=Subquery(gate.values('scope_value')[:1])),
            grants=ArraySubquery(grants),
        ))


def scope_fingerprint(scope):
    """Opaque, nonsecret comparison token; never accepts client-supplied scope.

    Only the explicit metadata projection above is hashed. Clinical values,
    patient data epochs, notes/evidence text, credentials and the query timestamp
    are deliberately absent. Active flags change at time boundaries, not on each
    refresh. Permission sets are canonical regardless of database/array order.
    """
    canonical = dict(scope)
    canonical['grants'] = sorted((dict(grant,
        field_codes=sorted(set(grant['field_codes']))) for grant in scope['grants']),
        key=lambda grant: grant['id'])
    encoded = json.dumps(canonical, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return sha256(encoded.encode('utf-8')).hexdigest()


def scope_actions(scope):
    """Keep relation-local action hints; real operations still use Policy Gate."""
    relation = scope['relation']
    gate = scope['environment']['gate']
    if (not relation['active'] or scope['principal']['account_status'] != 'active'
            or scope['patient']['status'] != 'active' or not gate or not gate['enabled']):
        return []
    return sorted({grant['action'] for grant in scope['grants']
        if grant['relation_id'] == relation['id'] and grant['active']})


def scope_valid_until(scope, now):
    """Earliest known future change, not a promise that access lasts until then.

    Include future grants and linked relations as well as currently active rows.
    The client must suspend/clear and revalidate at this boundary; only explicit
    null means no future temporal boundary is known in this snapshot.
    """
    rows = [scope['relation'], *scope['grants']]
    rows.extend(grant['relation'] for grant in scope['grants'] if grant['relation'] is not None)
    boundaries = [parse_datetime(row[key]) for row in rows
        for key in ('valid_from', 'valid_until', 'revoked_at') if row[key] is not None]
    upcoming = [boundary for boundary in boundaries if boundary > now]
    return min(upcoming).isoformat() if upcoming else None
