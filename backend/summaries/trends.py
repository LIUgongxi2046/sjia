"""Descriptive raw-value plots of immutable summary inputs, with no live reads.

This is not clinical trend qualification, normalization, or risk assessment.
Source definition revisions identify registered source contracts, never physical
report/document versions. Every point retains its own record and field identity.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def point(snapshot):
    medical = snapshot.get('medical_time')
    instant = None
    reasons = []
    if (snapshot.get('value_state') != 'known' or snapshot.get('raw_numeric') is None
            or snapshot.get('qualifier') != 'eq'):
        reasons.append('EXACT_KNOWN_VALUE_REQUIRED')
    else:
        try:
            if not Decimal(snapshot['raw_numeric']).is_finite():
                reasons.append('FINITE_VALUE_REQUIRED')
        except (InvalidOperation, TypeError, ValueError):
            reasons.append('FINITE_VALUE_REQUIRED')
    if not snapshot.get('raw_unit'):
        reasons.append('UNIT_UNKNOWN')
    if (not medical or medical.get('precision') not in ('minute', 'second')
            or medical.get('resolution_status') != 'resolved' or not medical.get('instant')
            or snapshot.get('sample_time_origin') == 'unknown'):
        reasons.append('EXACT_MEDICAL_TIME_REQUIRED')
    else:
        try:
            parsed = datetime.fromisoformat(medical['instant'])
            if parsed.utcoffset() is None:
                raise ValueError('offset required')
            instant = parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            reasons.append('EXACT_MEDICAL_TIME_REQUIRED')
    return {'field_id': snapshot['field_id'], 'record_version_id': snapshot['record_version_id'],
        'source_definition_revision_id': snapshot.get('source_definition_revision_id'),
        'plot_at': instant, 'raw_value': snapshot.get('raw_numeric'), 'raw_unit': snapshot.get('raw_unit'),
        'plottable': not reasons, 'reason_codes': reasons}


def build_summary_trends(snapshots, snapshot_sha256):
    """Pure projection: never fetch newer records/configuration or fill gaps."""
    groups = {}
    for snapshot in snapshots:
        if snapshot.get('value_type') == 'decimal':
            key = (snapshot.get('concept_code'), snapshot['field_code'])
            groups.setdefault(key, []).append((snapshot, point(snapshot)))
    series = []
    for (concept, code), values in sorted(groups.items(), key=lambda pair: (pair[0][0] or '', pair[0][1])):
        values.sort(key=lambda value: (value[1]['plot_at'] is None, value[1]['plot_at'] or '', value[1]['field_id']))
        counts = Counter(value['plot_at'] for _, value in values if value['plot_at'] is not None)
        unplaced = any(value['plot_at'] is None for _, value in values)
        segments = []
        for (before, left), (after, right) in zip(values, values[1:]):
            reasons = []
            if not left['plottable'] or not right['plottable']:
                reasons.append('POINT_NOT_PLOTTABLE')
            if unplaced:
                reasons.append('UNPOSITIONED_TIME_GAP')
            if not concept:
                reasons.append('CONCEPT_UNKNOWN')
            if left['plot_at'] is None or right['plot_at'] is None:
                reasons.append('EXACT_MEDICAL_TIME_REQUIRED')
            elif counts[left['plot_at']] > 1 or counts[right['plot_at']] > 1:
                reasons.append('DUPLICATE_MEDICAL_TIME')
            elif left['plot_at'] >= right['plot_at']:
                reasons.append('STRICT_TIME_ORDER_REQUIRED')
            if (before.get('medical_time') or {}).get('precision') != (after.get('medical_time') or {}).get('precision'):
                reasons.append('TIME_PRECISION_MISMATCH')
            for name, missing, mismatch in (
                    ('raw_unit', 'UNIT_UNKNOWN', 'UNIT_MISMATCH'),
                    ('method', 'METHOD_UNKNOWN', 'METHOD_MISMATCH'),
                    ('source_definition_revision_id', 'SOURCE_DEFINITION_UNKNOWN', 'SOURCE_DEFINITION_MISMATCH'),
                    ('source_organization', 'SOURCE_ORGANIZATION_UNKNOWN', 'SOURCE_ORGANIZATION_MISMATCH'),
                    ('origin_type', 'SOURCE_TYPE_UNKNOWN', 'SOURCE_TYPE_MISMATCH')):
                if not before.get(name) or not after.get(name):
                    reasons.append(missing)
                elif before[name] != after[name]:
                    reasons.append(mismatch)
            segments.append({'from_field_id': left['field_id'], 'to_field_id': right['field_id'],
                'connect': not reasons, 'reason_codes': list(dict.fromkeys(reasons))})
        series.append({'concept_code': concept, 'field_code': code,
            'points': [value for _, value in values], 'segments': segments})
    return {'schema_version': 'summary-raw-trend-1', 'snapshot_sha256': snapshot_sha256,
        'clinical_interpretation': False, 'series': series}
