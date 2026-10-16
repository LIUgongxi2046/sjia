"""No-database archive regressions; all identifiers and facts are synthetic."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from summaries.delta import build_items, qualified_sampling_relation
from archives.search_context import ArchiveSearchContext
from archives.search import matches, query_dates
from archives.search_schemas import checked_search
from records.queries import QueryRejected
from records.models import Record, RecordField, RecordVersion


class DeltaSamplingQualificationTests(SimpleTestCase):
    def setUp(self):
        self.proof = SimpleNamespace(id=uuid4(), verifier_id=uuid4(), revoked_at=None,
            verified_at=timezone.now() - timedelta(minutes=1), evidence_ref='synthetic:relation-evidence')
        self.facts = [{'fact': SimpleNamespace(id=uuid4(), fact_id=uuid4(), version=1),
            'value': {'verification_status': 'verified', 'medical_time': {'precision': 'second'}},
            'proof': self.proof if index == 0 else None, 'sources': [{'synthetic_source': str(index)}]}
            for index in range(2)]
        self.relation = SimpleNamespace(id=uuid4(), status='confirmed', relation_type='same_sampling',
            verification_id=self.proof.id, author_id=self.proof.verifier_id, evidence_ref=self.proof.evidence_ref,
            left_fact_version_id=self.facts[0]['fact'].id, right_fact_version_id=self.facts[1]['fact'].id)

    def items(self):
        with patch('summaries.delta.checked_delta', side_effect=lambda _name, value: value):
            return build_items({'baseline_document_version_ids': [], 'current_document_version_ids': []},
                {}, {'current': self.facts, 'baseline': []}, [self.relation], None)

    def test_family_confirmation_without_proof_preserves_both_facts(self):
        self.relation.verification_id = None
        items = self.items()
        self.assertEqual(sum(item['kind'] == 'fact' for item in items), 2)
        self.assertFalse(any(item['kind'] == 'duplicate' for item in items))
        self.assertTrue(any('SAMPLING_VERIFICATION_REQUIRED' in item['reason_codes'] for item in items))

    def test_current_correctly_bound_professional_proof_deduplicates(self):
        self.assertTrue(qualified_sampling_relation(self.relation, self.facts))
        items = self.items()
        self.assertEqual(sum(item['kind'] == 'fact' for item in items), 1)
        self.assertEqual(sum(item['kind'] == 'duplicate' for item in items), 1)

    def test_revoked_future_wrong_author_or_evidence_cannot_deduplicate(self):
        mutations = [('revoked_at', timezone.now()), ('verified_at', timezone.now() + timedelta(days=1)),
            ('verifier_id', uuid4()), ('evidence_ref', 'synthetic:different-evidence')]
        for key, value in mutations:
            with self.subTest(key=key):
                old = getattr(self.proof, key)
                setattr(self.proof, key, value)
                self.assertFalse(qualified_sampling_relation(self.relation, self.facts))
                self.assertEqual(sum(item['kind'] == 'fact' for item in self.items()), 2)
                setattr(self.proof, key, old)

    def test_unrelated_qualified_proof_is_not_a_relation_proof(self):
        self.relation.verification_id = uuid4()
        self.assertFalse(qualified_sampling_relation(self.relation, self.facts))

    def test_missing_endpoint_and_nonconfirmed_relation_stay_unqualified(self):
        self.assertFalse(qualified_sampling_relation(self.relation, self.facts[:1]))
        for status in ('proposed', 'disputed', 'released'):
            self.relation.status = status
            self.assertFalse(qualified_sampling_relation(self.relation, self.facts))

    def test_independent_relation_never_merges_same_batch_facts(self):
        self.relation.relation_type = 'independent_sampling'
        self.assertEqual(sum(item['kind'] == 'fact' for item in self.items()), 2)


class ArchiveCurrentFieldLineageTests(SimpleTestCase):
    def setUp(self):
        self.context = ArchiveSearchContext.__new__(ArchiveSearchContext)
        self.context.tables = {}
        record = SimpleNamespace(id=uuid4(), current_version=2, status='active')
        self.old = SimpleNamespace(id=uuid4(), record_id=record.id, version=1)
        self.current = SimpleNamespace(id=uuid4(), record_id=record.id, version=2)
        self.original = SimpleNamespace(id=uuid4(), record_version_id=self.old.id,
            ordinal=1, field_code='report_item')
        self.corrected = SimpleNamespace(id=uuid4(), record_version_id=self.current.id,
            ordinal=1, field_code='report_item')
        self.unrelated = SimpleNamespace(id=uuid4(), record_version_id=self.current.id,
            ordinal=2, field_code='report_item')
        self.context.store(Record, [record])
        self.context.store(RecordVersion, [self.old, self.current])
        self.context.store(RecordField, [self.original, self.corrected, self.unrelated])

    def test_correction_does_not_add_other_same_code_fields(self):
        self.assertEqual(self.context.current_fields({'fields': [(self.original, self.old)]}),
            [(self.corrected, self.current)])

    def test_missing_ordinal_never_falls_back_to_unrelated_field(self):
        self.context.store(RecordField, [self.original, self.unrelated])
        self.assertEqual(self.context.current_fields({'fields': [(self.original, self.old)]}), [])


class ArchiveEncounterTimeFilterTests(SimpleTestCase):
    def setUp(self):
        self.version = SimpleNamespace(document_id=uuid4(), document_type='inpatient', grouping_status='confirmed')
        self.episode = uuid4()
        self.scope = {'episodes': [{'episode_id': str(self.episode)}], 'episode_times': [
            {'episode_id': self.episode, 'start': self.time('2026-10-01'), 'end': self.time('2026-10-05')}]}
        self.document_time = self.time('2026-10-10')

    def time(self, value):
        return SimpleNamespace(precision='date', resolution_status='local_only', local_date=date.fromisoformat(value),
            timezone_name=None, utc_offset_minutes=None)

    def match(self, **query):
        checked_search('ArchiveSearchInput', query)
        return matches(self.version, self.scope, [], 'unverified', query, query_dates(query), self.document_time, None, set())

    def test_default_is_document_date_but_episode_basis_matches_encounter_interval(self):
        self.assertFalse(self.match(from_date='2026-10-03', to_date='2026-10-03'))
        self.assertTrue(self.match(time_basis='episode', from_date='2026-10-03', to_date='2026-10-03'))
        self.assertFalse(self.match(time_basis='episode', from_date='2026-10-09', to_date='2026-10-10'))

    def test_unknown_encounter_boundary_never_uses_document_date(self):
        self.scope['episode_times'][0]['end'] = None
        self.assertFalse(self.match(time_basis='episode', from_date='2026-10-10'))
        self.assertTrue(self.match(time_basis='episode', from_date='2026-10-10', include_unknown_time=True))

    def test_selected_encounter_only_and_multi_encounter_any_match(self):
        second = uuid4()
        self.scope['episodes'].append({'episode_id': str(second)})
        self.scope['episode_times'].append({'episode_id': second, 'start': self.time('2026-09-01'), 'end': self.time('2026-09-03')})
        self.assertTrue(self.match(time_basis='episode', from_date='2026-09-02', to_date='2026-09-02'))
        self.assertFalse(self.match(time_basis='episode', episode_id=str(self.episode), from_date='2026-09-02', to_date='2026-09-02'))

    def test_no_encounter_is_unknown_and_invalid_basis_is_rejected(self):
        self.scope = {'episodes': [], 'episode_times': []}
        self.assertFalse(self.match(time_basis='episode', from_date='2026-01-01'))
        self.assertTrue(self.match(time_basis='episode', from_date='2026-01-01', include_unknown_time=True))
        with self.assertRaises(QueryRejected):
            self.match(time_basis='upload')

