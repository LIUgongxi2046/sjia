"""Explicit per-rule release bindings. Never infers clinical ranking or shared scope."""
from types import SimpleNamespace
from uuid import UUID

from django.db import transaction

from configurations.models import ConfigVersion
from family_content.models import ReleaseBundle, ReleaseComponent, ReleasePublicationGroup

GROUP_KINDS = ('rule', 'window', 'verification', 'mapping', 'content', 'validation')
SHARED_KINDS = ('visibility', 'notification')
PRIORITY_ORDERS = ('higher_first', 'lower_first')


def publication_groups(release, *, lock=False):
    rows = ReleasePublicationGroup.objects.filter(release_id=release.id).order_by('rule_id', 'id')
    if lock:
        # Schema-qualified quoted model tables cannot use PostgreSQL FOR UPDATE OF.
        # Lock only this table without joins; configurations are locked separately.
        list(rows.select_for_update())
    # Avoid six self-joins of config_version, including the empty-draft check.
    # Prefetch preserves exact FK bindings and leaves config locks to the caller.
    return list(rows.prefetch_related(*GROUP_KINDS))


def group_input_reason(payload):
    groups = payload.get('publication_groups')
    if groups is None:
        kinds = [row['kind'] for row in payload['components']]
        return 'RELEASE_COMPONENT_DUPLICATE' if len(kinds) != len(set(kinds)) else None
    if len(groups) < 2 or len({row['rule_id'] for row in groups}) != len(groups):
        return 'RELEASE_GROUP_RULE_DUPLICATE'
    pairs = [(row['kind'], row['config_id']) for row in payload['components']]
    if len(pairs) != len(set(pairs)):
        return 'RELEASE_COMPONENT_DUPLICATE'
    required = {(kind, group[kind + '_id']) for group in groups for kind in GROUP_KINDS if kind != 'validation'}
    shared = {pair for pair in pairs if pair[0] in SHARED_KINDS}
    if any(sum(pair[0] == kind for pair in shared) != 1 for kind in SHARED_KINDS) or set(pairs) != required | shared:
        return 'RELEASE_GROUP_COMPONENT_MISMATCH'
    if payload['validation_config_id'] not in {row['validation_id'] for row in groups}:
        return 'RELEASE_GROUP_VALIDATION_MISMATCH'
    return None


def group_components(components, group):
    shared = [item for item in components if item.component_kind in SHARED_KINDS]
    return shared + [SimpleNamespace(component_kind=kind, config=getattr(group, kind), config_id=getattr(group, kind + '_id'))
        for kind in GROUP_KINDS if kind != 'validation']


def grouped_component_reason(release, components, groups):
    payload = {'components': [{'kind': row.component_kind, 'config_id': str(row.config_id)} for row in components],
        'validation_config_id': str(release.validation_config_id),
        'publication_groups': [{kind + '_id': str(getattr(group, kind + '_id')) for kind in GROUP_KINDS} for group in groups]}
    reason = group_input_reason(payload)
    if reason:
        return reason
    if any(getattr(group, kind).kind != kind for group in groups for kind in GROUP_KINDS):
        return 'RELEASE_COMPONENT_KIND_MISMATCH'
    return None


def group_priority_reason(groups):
    orders = {group.rule.payload.get('publication', {}).get('priority_order', 'unconfigured') for group in groups}
    if len(orders) != 1 or not orders <= set(PRIORITY_ORDERS):
        return 'RELEASE_PRIORITY_ORDER_UNCONFIGURED'
    keys = [group.rule.payload.get('rule_key') for group in groups]
    if any(not isinstance(key, str) or not key.strip() for key in keys) or len(keys) != len(set(keys)):
        return 'RELEASE_GROUP_RULE_KEY_DUPLICATE'
    return None


@transaction.atomic
def add_publication_groups(*, release, groups):
    from family_content.service import ReleaseRejected
    release = ReleaseBundle.objects.select_for_update().get(id=release.id)
    if (release.status != 'draft' or ReleaseComponent.objects.filter(release=release).exists()
            or ReleasePublicationGroup.objects.filter(release=release).exists()):
        raise ReleaseRejected('RELEASE_GROUPS_NOT_EDITABLE')
    ids = {UUID(value) for group in groups for value in group.values()}
    configs = {row.id: row for row in ConfigVersion.objects.select_for_update().filter(id__in=ids).order_by('id')}
    if len(groups) < 2 or len({group['rule_id'] for group in groups}) != len(groups):
        raise ReleaseRejected('RELEASE_GROUP_RULE_DUPLICATE')
    for group in groups:
        if any(UUID(group[kind + '_id']) not in configs or configs[UUID(group[kind + '_id'])].kind != kind for kind in GROUP_KINDS):
            raise ReleaseRejected('RELEASE_COMPONENT_KIND_MISMATCH')
        ReleasePublicationGroup.objects.create(release=release,
            **{kind + '_id': UUID(group[kind + '_id']) for kind in GROUP_KINDS})


def matching_group(release, rule_id):
    return ReleasePublicationGroup.objects.filter(release_id=release.id, rule_id=rule_id).prefetch_related(*GROUP_KINDS).first()
