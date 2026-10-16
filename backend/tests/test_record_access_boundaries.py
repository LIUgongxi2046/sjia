"""Synthetic-only regressions for original downloads and field-scoped queries.

Run: python backend/manage.py test tests.test_record_access_boundaries
No model providers, external services, or real patient data are used.
"""
from datetime import date, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from django.test import TestCase, override_settings
from django.utils import timezone

from archives.record_facts import create_record_facts
from authorization.models import AccessGrant
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject
from identity.models import Principal
from medical_time.models import MedicalTime
from records.access import query_permissions
from records.models import Record, RecordField, RecordVersion
from records.queries import QueryRejected, history_search, record_search
from relations.models import Relation, RelationApplication
from tests import test_T16 as record_tests


@override_settings(SJIA_ENVIRONMENT='test')
class RecordAccessBoundaryTest(TestCase):
    def setUp(self):
        record_tests.RecordQueryTest.setUp(self)
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.principal, case=self)
        # These TestCase tests cover permission filtering inside the outer test
        # transaction. Real snapshot isolation is tested by SnapshotHTTPTransactionTests.
        snapshot = patch('records.history.begin_history_snapshot')
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.now = timezone.now()
        self.other = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        EnvironmentGate.objects.create(environment='test', version=1, enabled=True, updated_at=self.now)
        application = RelationApplication.objects.create(version=1, applicant=self.principal, patient=self.patient,
            evidence_ref='synthetic-test-only', status='approved', checked_by=self.principal,
            checked_at=self.now, created_at=self.now)
        self.relation = Relation.objects.create(patient=self.patient, guardian=self.principal, application=application,
            status='active', version=1, checked_by=self.principal, checked_at=self.now,
            valid_from=self.now - timedelta(days=1))
        session = self.client.session
        session['_sjia_principal_id'] = str(self.principal.id)
        session['_sjia_auth_epoch'] = self.principal.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()

    def _grant(self, *, action='read_raw', codes=('allowed',), scope='all', assigned=None, **kwargs):
        return AccessGrant.objects.create(patient=self.patient, principal=self.principal, relation_id=self.relation.id,
            action=action, field_codes=list(codes), record_scope=scope, assigned_record_id=assigned,
            purpose='family_observation', granted_by=self.principal, evidence_ref='synthetic-test-only',
            valid_from=self.now - timedelta(days=1), **kwargs)

    def _time(self, index, text=None):
        return MedicalTime.objects.create(id=UUID(int=index), patient=self.patient, precision='date',
            local_date=date(2026, 1, index), resolution_status='local_only', raw_text=text)

    def _record(self, codes=('allowed',), *, actor=None, times=None):
        record = Record.objects.create(patient=self.patient, kind='observation', current_version=1,
            status='active', created_at=self.now)
        version = RecordVersion.objects.create(patient=self.patient, record_id=record.id, version=1,
            author=actor or self.principal, origin_type='self_report', capture_method='manual',
            source_revision=self.use_auth.source_revision, use_authorization_id=self.use_auth.id,
            quality_status='valid', quality_reasons=[], recorded_at=self.now, retention_policy=self.retention)
        for index, code in enumerate(codes):
            RecordField.objects.create(patient=self.patient, record_version_id=version.id, ordinal=index + 1,
                field_code=code, concept_code=f'{code}-concept', original_name=f'{code}-name',
                value_type='text', value_state='known', raw_text=f'{code}-value',
                medical_time_id=times[index].id if times and times[index] else None,
                sample_time_origin='observation', quality_reasons=[])
        create_record_facts(version)
        return record

    def _permissions(self):
        return query_permissions(self.principal, self.patient)

    def _history(self, **kwargs):
        return history_search(patient=self.patient, principal=self.principal,
            permission_scopes=self._permissions(), **kwargs)['entries']

    def _search(self, **kwargs):
        return record_search(patient=self.patient, principal=self.principal,
            permission_scopes=self._permissions(), **kwargs)

    def _file(self, owner=None):
        group = FileGroup.objects.create(patient=self.patient, owner=owner or self.principal,
            version=1, status='available', source_revision=self.use_auth.source_revision,
            use_authorization_id=self.use_auth.id, created_at=self.now)
        return FileObject.objects.create(patient=self.patient, file_group_id=group.id,
            object_key=f'synthetic/{uuid4()}', object_version='1', checksum='a' * 64, size_bytes=9,
            media_type='application/pdf', purpose='original', status='available',
            retention_policy=self.retention, created_at=self.now)

    def test_field_and_page_grants_cannot_download_original_or_range(self):
        file = self._file()
        for codes in [('allowed',), ('page:1',)]:
            grant = self._grant(action='read_file', codes=codes)
            for headers in [{}, {'HTTP_RANGE': 'bytes=0-4'}]:
                with self.subTest(codes=codes, headers=headers), patch('files.service.object_store.get') as read:
                    response = self.client.get(f'/api/files/{file.id}/content', **headers)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()['code'], 'CASE_ACTION_DENIED')
                    read.assert_not_called()
            grant.delete()

    def test_whole_original_and_range_remain_readable_with_whole_grant(self):
        file = self._file()
        self._grant(action='read_file', codes=())
        with patch('files.service.object_store.get', return_value=b'%PDF-test') as read:
            full = self.client.get(f'/api/files/{file.id}/content')
            self.assertEqual(full.status_code, 200)
            self.assertEqual(full.content, b'%PDF-test')
            ranged = self.client.get(f'/api/files/{file.id}/content', HTTP_RANGE='bytes=0-4')
            self.assertEqual(ranged.status_code, 206)
            self.assertEqual(ranged.content, b'%PDF-')
            self.assertEqual(ranged['Content-Range'], 'bytes 0-4/9')
            self.assertEqual(read.call_count, 2)

    def test_whole_original_own_grant_checks_group_owner(self):
        own, other = self._file(), self._file(owner=self.other)
        self._grant(action='read_file', codes=(), scope='own')
        with patch('files.service.object_store.get', return_value=b'%PDF-test') as read:
            self.assertEqual(self.client.get(f'/api/files/{own.id}/content').status_code, 200)
            read.reset_mock()
            self.assertEqual(self.client.get(f'/api/files/{other.id}/content').status_code, 403)
            read.assert_not_called()

    def test_expired_or_revoked_whole_grants_do_not_read_storage(self):
        file = self._file()
        for condition in [{'revoked_at': self.now}, {'valid_until': self.now}]:
            grant = self._grant(action='read_file', codes=(), **condition)
            with self.subTest(condition=condition), patch('files.service.object_store.get') as read:
                self.assertEqual(self.client.get(f'/api/files/{file.id}/content').status_code, 403)
                read.assert_not_called()
            grant.delete()

    def test_history_excludes_hidden_and_unreferenced_times_before_limit(self):
        hidden, visible, unused = self._time(1, 'hidden-only'), self._time(2, 'visible'), self._time(3, 'unused')
        self._record(('hidden', 'allowed'), times=[hidden, visible])
        self._grant()
        entries = self._history(limit=1)
        self.assertEqual([entry['medical_time_id'] for entry in entries], [str(visible.id)])
        self.assertEqual(entries[0]['raw_text'], 'visible')
        self.assertEqual(entries[0]['field_count'], 1)
        response = self.client.post(f'/api/patients/{self.patient.id}/history/search', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['entries'], entries)
        self.assertNotIn(str(hidden.id), response.content.decode())
        self.assertNotIn(str(unused.id), response.content.decode())

    def test_history_counts_only_visible_fields_without_double_counting_grants(self):
        shared = self._time(1)
        self._record(('hidden', 'allowed', 'allowed'), times=[shared, shared, shared])
        self._grant()
        self._grant()
        self.assertEqual(self._history()[0]['field_count'], 2)

    def test_history_own_and_assigned_grants_preserve_record_field_pairs(self):
        own_time, assigned_time, other_time = self._time(1), self._time(2), self._time(3)
        self._record(('allowed', 'hidden'), times=[own_time, own_time])
        assigned = self._record(('allowed', 'hidden'), actor=self.other, times=[assigned_time, assigned_time])
        self._record(('allowed', 'hidden'), actor=self.other, times=[other_time, other_time])
        self._grant(scope='own')
        self._grant(scope='assigned', codes=('hidden',), assigned=assigned.id)
        entries = self._history()
        self.assertEqual([entry['medical_time_id'] for entry in entries], [str(own_time.id), str(assigned_time.id)])
        self.assertEqual([entry['field_count'] for entry in entries], [1, 1])
        response = self.client.post(f'/api/patients/{self.patient.id}/history/search', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['entries'], entries)

    def test_history_denies_missing_expired_and_revoked_permissions(self):
        self._record(times=[self._time(1)])
        path = f'/api/patients/{self.patient.id}/history/search'
        self.assertEqual(self._history(), [])
        self.assertEqual(self.client.post(path, {}, content_type='application/json').status_code, 403)
        self._grant(valid_until=self.now)
        self._grant(revoked_at=self.now)
        self.assertEqual(self._history(), [])
        self.assertEqual(self.client.post(path, {}, content_type='application/json').status_code, 403)

    def test_history_denies_revoked_relation(self):
        self._record(times=[self._time(1)])
        self._grant()
        self.relation.status = 'revoked'
        self.relation.revoked_at = self.now
        self.relation.save(update_fields=['status', 'revoked_at'])
        response = self.client.post(f'/api/patients/{self.patient.id}/history/search', {}, content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_history_keeps_environment_gate_before_data_read(self):
        self._grant()
        EnvironmentGate.objects.filter(environment='test').update(enabled=False)
        with patch('api.views_family2.history_search') as search:
            response = self.client.post(f'/api/patients/{self.patient.id}/history/search', {}, content_type='application/json')
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['code'], 'ENVIRONMENT_DISABLED')
            search.assert_not_called()

    def test_history_does_not_reveal_superseded_or_withdrawn_records(self):
        old_time, current_time, withdrawn_time = self._time(1), self._time(2), self._time(3)
        record = self._record(times=[old_time])
        from records.correction import correct_record

        correct_record(record=record, expected_version=1, actor=self.principal, reason='Synthetic correction',
            fields=[{'field_code': 'allowed', 'concept_code': 'allowed-concept',
                'value_type': 'text', 'value_state': 'known', 'raw_text': 'new',
                'medical_time': {'precision': 'date', 'local_date': current_time.local_date.isoformat(),
                    'resolution_status': 'local_only'}, 'sample_time_origin': 'observation'}],
            use_authorization_id=self.use_auth.id, retention_policy=self.retention,
            source_revision=self.use_auth.source_revision)
        withdrawn = self._record(times=[withdrawn_time])
        withdrawn.status = 'withdrawn'
        withdrawn.save(update_fields=['status'])
        self._grant()
        entries = self._history()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['local_date'], current_time.local_date.isoformat())
        self.assertNotEqual(entries[0]['medical_time_id'], str(old_time.id))

    def test_history_limit_is_bounded(self):
        for limit in [0, -1, 101, True, '20']:
            with self.subTest(limit=limit), self.assertRaises(QueryRejected) as exc:
                self._history(limit=limit)
            self.assertEqual(exc.exception.reason_code, 'LIMIT_INVALID')

    def test_record_search_serializes_only_permitted_fields_and_fact_references(self):
        record = self._record(('allowed', 'hidden'))
        self._grant()
        result = self._search()['records']
        self.assertEqual([entry['record_id'] for entry in result], [str(record.id)])
        detail = result[0]['detail']
        self.assertEqual([field['field_code'] for field in detail['fields']], ['allowed'])
        self.assertEqual([ref['field_id'] for ref in detail['fact_refs']], [detail['fields'][0]['field_id']])
        self.assertNotIn('hidden-value', str(detail))

    def test_record_search_predicates_cannot_match_hidden_fields(self):
        visible, hidden = self._time(1), self._time(2)
        self._record(('allowed', 'hidden'), times=[visible, hidden])
        self._grant()
        for filters in [{'concept_code': 'hidden-concept'}, {'original_name': 'hidden-name'},
                        {'from_date': hidden.local_date.isoformat()}, {'field_codes': ['hidden']}]:
            with self.subTest(filters=filters):
                self.assertEqual(self._search(**filters)['records'], [])

    def test_record_search_pagination_and_order_ignore_hidden_fields(self):
        old, recent, hidden_recent = self._time(1), self._time(2), self._time(3)
        older = self._record(('allowed', 'hidden'), times=[old, hidden_recent])
        newer = self._record(times=[recent])
        self._record(('hidden',), times=[hidden_recent])
        self._grant()
        first = self._search(limit=1)
        self.assertEqual(first['records'][0]['record_id'], str(newer.id))
        self.assertIsNotNone(first['next_cursor'])
        second = self._search(limit=1, cursor=first['next_cursor'])
        self.assertEqual(second['records'][0]['record_id'], str(older.id))
        self.assertIsNone(second['next_cursor'])
        self.assertEqual([field['field_code'] for field in second['records'][0]['detail']['fields']], ['allowed'])

    def test_record_search_serialization_preserves_scope_pairs(self):
        own = self._record(('allowed', 'hidden'))
        assigned = self._record(('allowed', 'hidden'), actor=self.other)
        self._record(('allowed', 'hidden'), actor=self.other)
        self._grant(scope='own')
        self._grant(scope='assigned', codes=('hidden',), assigned=assigned.id)
        records = {entry['record_id']: entry['detail']['fields'] for entry in self._search()['records']}
        self.assertEqual(set(records), {str(own.id), str(assigned.id)})
        self.assertEqual([field['field_code'] for field in records[str(own.id)]], ['allowed'])
        self.assertEqual([field['field_code'] for field in records[str(assigned.id)]], ['hidden'])


    def test_source_own_scope_uses_original_record_author_not_later_corrector(self):
        record = self._record(actor=self.other)
        correction = RecordVersion.objects.create(patient=self.patient, record_id=record.id,
            version=2, supersedes_version=1, correction_reason='Synthetic scope regression',
            corrected_at=self.now, author=self.principal, origin_type='self_report', capture_method='manual',
            source_revision=self.use_auth.source_revision, use_authorization_id=self.use_auth.id,
            quality_status='valid', quality_reasons=[], recorded_at=self.now,
            retention_policy=self.retention)
        field = RecordField.objects.create(patient=self.patient, record_version_id=correction.id,
            ordinal=1, field_code='allowed', value_type='text', value_state='known',
            raw_text='Synthetic corrected field', sample_time_origin='observation', quality_reasons=[])
        record.current_version = 2
        record.save(update_fields=['current_version'])
        self._grant(action='read_raw', codes=('allowed',), scope='own')
        response = self.client.get(f'/api/record-versions/{correction.id}/fields/{field.id}/source')
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json()['code'], 'CASE_ACTION_DENIED')
