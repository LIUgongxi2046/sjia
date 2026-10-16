"""Synthetic software-only per-rule release grouping and explicit-order contracts."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from alerting.lifecycle import publication_reason
from alerting.models import AlertEvaluation, AlertEpisode, AlertEvent
from api.views_releases import release_view
from api.views_publication import publication_release_view
from configurations.models import ConfigVersion
from family_content.alert_detail import alert_content, select_current_event
from family_content.models import ReleaseComponent, ReleasePublicationGroup
from family_content.publication_groups import (GROUP_KINDS, add_publication_groups, group_input_reason,
    group_priority_reason, publication_groups)
from family_content.qualification import release_qualification_reason
from family_content.service import (ReleaseRejected, add_release_component, approve_release,
    create_release_bundle, enable_release, release_available)
from identity.models import Patient, Principal
from tasks.models import Task
from sources.models import DataUseAuthorization
from tests.synthetic_fixtures import SyntheticEvidence, synthetic_publication


class ExplicitPriorityTest(SimpleTestCase):
    def events(self, order):
        now = timezone.now()
        return [NS(id=uuid4(), created_at=now, trigger_policy=NS(payload={'rule_key': key,
            'publication': {'priority': rank, **({'priority_order': order} if order else {})}}))
            for key, rank in [('alpha', 1), ('beta', 2)]]

    def test_both_directions_follow_only_explicit_setting(self):
        for order, expected in [('higher_first', 'beta'), ('lower_first', 'alpha')]:
            event, reason = select_current_event(self.events(order))
            self.assertIsNone(reason); self.assertEqual(event.trigger_policy.payload['rule_key'], expected)

    def test_missing_or_conflicting_direction_is_unavailable(self):
        for order in (None, 'unconfigured'):
            self.assertEqual(select_current_event(self.events(order)), (None, 'ALERT_PRIORITY_ORDER_UNCONFIGURED'))
        events = self.events('higher_first'); events[0].trigger_policy.payload['publication']['priority_order'] = 'lower_first'
        self.assertEqual(select_current_event(events), (None, 'ALERT_PRIORITY_ORDER_UNCONFIGURED'))

    def test_ties_are_rule_key_then_newest(self):
        events = self.events('higher_first'); events[0].trigger_policy.payload['publication']['priority'] = 2
        self.assertEqual(select_current_event(events)[0], events[0])
        newer = deepcopy(events[0]); newer.created_at += timedelta(seconds=1); newer.id = uuid4()
        self.assertEqual(select_current_event([*events, newer])[0], newer)

    def test_group_order_and_rule_keys_must_agree(self):
        events = self.events('higher_first'); groups = [NS(rule=event.trigger_policy) for event in events]
        self.assertIsNone(group_priority_reason(groups))
        groups[1].rule.payload['publication'].pop('priority_order')
        self.assertEqual(group_priority_reason(groups), 'RELEASE_PRIORITY_ORDER_UNCONFIGURED')
        groups[1].rule.payload['publication']['priority_order'] = 'higher_first'
        groups[1].rule.payload['rule_key'] = 'alpha'
        self.assertEqual(group_priority_reason(groups), 'RELEASE_GROUP_RULE_KEY_DUPLICATE')

    def test_exact_component_union_and_distinct_rule_ids(self):
        groups = [{kind + '_id': str(uuid4()) for kind in GROUP_KINDS} for _ in range(2)]
        payload = {'publication_groups': groups, 'validation_config_id': groups[0]['validation_id'],
            'components': [{'kind': kind, 'config_id': group[kind + '_id']} for group in groups for kind in GROUP_KINDS if kind != 'validation']
            + [{'kind': kind, 'config_id': str(uuid4())} for kind in ('visibility', 'notification')]}
        self.assertIsNone(group_input_reason(payload))
        missing = deepcopy(payload); missing['components'].pop()
        self.assertEqual(group_input_reason(missing), 'RELEASE_GROUP_COMPONENT_MISMATCH')
        extra = deepcopy(payload); extra['components'].append({'kind': 'content', 'config_id': str(uuid4())})
        self.assertEqual(group_input_reason(extra), 'RELEASE_GROUP_COMPONENT_MISMATCH')
        duplicate = deepcopy(payload); duplicate['publication_groups'][1]['rule_id'] = duplicate['publication_groups'][0]['rule_id']
        self.assertEqual(group_input_reason(duplicate), 'RELEASE_GROUP_RULE_DUPLICATE')
        foreign = deepcopy(payload); foreign['validation_config_id'] = str(uuid4())
        self.assertEqual(group_input_reason(foreign), 'RELEASE_GROUP_VALIDATION_MISMATCH')


@override_settings(SJIA_ENVIRONMENT='test')
class PublicationGroupLifecycleTest(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.fixture = SyntheticEvidence(self, self.actor)
        self.legacy, self.contact, self.common = synthetic_publication(self.fixture, add_components=False, enable=False)
        self.gate = self.fixture.gate

    def make_group(self, index, order='higher_first', shared_visibility=None):
        rule_id, content_id = uuid4(), uuid4()
        payload = deepcopy(self.common['rule'].payload)
        payload['rule_key'] = f'synthetic-rule-{index}'
        payload['episode']['rule_family_key'] = f'synthetic-family-{index}'
        payload['publication'].update(priority=index + 1, content_config_id=str(content_id))
        if order is not None: payload['publication']['priority_order'] = order
        if shared_visibility is not None: payload['publication']['visibility_config_id'] = str(shared_visibility)
        rule = self.fixture.configuration('rule', payload, identity=rule_id)
        content_payload = {**deepcopy(self.common['content'].payload), 'rule_id': str(rule_id),
            'approved_text': f'SYNTHETIC GROUP {index}; NO CLINICAL APPROVAL'}
        content = self.fixture.configuration('content', content_payload, identity=content_id)
        proof = rule.validations.get()
        validation = self.fixture.configuration('validation', {**deepcopy(self.common['validation'].payload),
            'rule_id': str(rule_id), 'report_ref': proof.evidence_ref, 'report_sha256': proof.evidence_sha256})
        return {**{kind: self.common[kind] for kind in ('window', 'mapping', 'verification')},
            'rule': rule, 'content': content, 'validation': validation}

    def grouped(self, orders=('higher_first', 'higher_first'), shared_visibility=None):
        groups = [self.make_group(index, order, shared_visibility if index == 1 else None) for index, order in enumerate(orders)]
        release = create_release_bundle(environment='test', contact_version=self.contact, validation_config=groups[0]['validation'])
        add_publication_groups(release=release, groups=[{kind + '_id': str(group[kind].id) for kind in GROUP_KINDS} for group in groups])
        components = {(kind, group[kind].id): group[kind] for group in groups for kind in GROUP_KINDS if kind != 'validation'}
        components.update({(kind, self.common[kind].id): self.common[kind] for kind in ('visibility', 'notification')})
        for (kind, _), config in components.items(): add_release_component(release=release, component_kind=kind, config=config)
        approve_release(release=release)
        return release, groups

    def enable(self, release):
        self.gate.refresh_from_db()
        return enable_release(release=release, environment_gate=self.gate, expected_gate_version=self.gate.version)

    def test_group_reads_never_build_six_config_self_joins(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(publication_groups(self.legacy), [])
        self.assertEqual(len(queries), 1)
        self.assertNotIn('JOIN', queries[0]['sql'].upper())
        release, _ = self.grouped()
        with CaptureQueriesContext(connection) as queries:
            groups = publication_groups(release)
            self.assertEqual(len(groups), 2)
            for group in groups:
                for kind in GROUP_KINDS: self.assertIsNotNone(getattr(group, kind).id)
        self.assertLessEqual(len(queries), 7)
        self.assertFalse(any('JOIN' in row['sql'].upper() for row in queries))

    def test_complete_grouped_release_qualifies_all_groups_and_roundtrips(self):
        release, groups = self.grouped(); self.enable(release)
        self.assertTrue(release_available(release))
        view = release_view(release)
        self.assertEqual({group['rule_id'] for group in view['release']['publication_groups']}, {str(group['rule'].id) for group in groups})
        detail = publication_release_view(release, self.gate)
        self.assertIsNone(detail['approved_text']); self.assertEqual(len(detail['approved_contents']), 2)
        self.assertTrue(all(row['state'] == 'available' for row in detail['readiness']))

    def test_unconfigured_and_conflicting_groups_cannot_enable(self):
        for orders in [(None, None), ('higher_first', 'lower_first')]:
            release, _ = self.grouped(orders)
            with self.assertRaisesMessage(ReleaseRejected, 'RELEASE_PRIORITY_ORDER_UNCONFIGURED'): self.enable(release)
        self.gate.refresh_from_db(); self.assertIsNone(self.gate.active_release_id)

    def test_shared_visibility_must_be_explicitly_same_reference(self):
        release, _ = self.grouped(shared_visibility=uuid4())
        with self.assertRaisesMessage(ReleaseRejected, 'RELEASE_POLICY_BINDING_MISMATCH'): self.enable(release)

    def test_revoking_any_group_validation_blocks_entire_release(self):
        release, groups = self.grouped(); self.enable(release)
        groups[1]['validation'].status = 'disabled'; groups[1]['validation'].save(update_fields=['status'])
        self.assertFalse(release_available(release))

    def test_grouped_lifecycle_uses_exact_rule_group_not_union(self):
        release, groups = self.grouped(); self.enable(release); self.gate.refresh_from_db()
        for group in groups:
            inputs = NS(policy=group['rule'], window=group['window'], context={'verification_policy_id': str(group['verification'].id)}, configuration_rows=[group['mapping']])
            self.assertEqual(publication_reason(inputs, self.gate), (release, None))
            inputs.window = self.common['content']
            self.assertEqual(publication_reason(inputs, self.gate)[1], 'DYNAMIC_RELEASE_REFERENCE_MISMATCH')

    def test_legacy_single_group_still_enables_without_order_setting(self):
        for kind in ('rule', 'window', 'verification', 'mapping', 'visibility', 'content', 'notification'):
            add_release_component(release=self.legacy, component_kind=kind, config=self.common[kind])
        approve_release(release=self.legacy); self.enable(self.legacy)
        self.assertTrue(release_available(self.legacy)); self.assertNotIn('publication_groups', release_view(self.legacy)['release'])

    def test_group_edit_after_approval_and_undeclared_component_are_denied(self):
        release, groups = self.grouped()
        with self.assertRaisesMessage(ReleaseRejected, 'RELEASE_GROUPS_NOT_EDITABLE'):
            add_publication_groups(release=release, groups=[{kind + '_id': str(group[kind].id) for kind in GROUP_KINDS} for group in groups])
        self.assertEqual(ReleasePublicationGroup.objects.filter(release=release).count(), 2)

    def test_each_current_event_uses_its_own_group_content(self):
        release, groups = self.grouped(); self.enable(release); self.gate.refresh_from_db()
        patient = Patient.objects.create(display_id='SYNTHETIC-GROUP-PROJECTION', status='active', created_at=timezone.now())
        now = timezone.now()
        authorization = DataUseAuthorization.objects.create(patient=patient, source_revision=self.fixture.source,
            purpose='software_test', environment='test', approved_by=self.actor, approved_at=now, evidence_ref=self.fixture.reference)
        for index, group in enumerate(groups):
            task = Task.objects.create(actor=self.actor, patient=patient, task_type='alert_evaluation', schema_version='synthetic',
                input_refs={}, input_sha256='a' * 64, status='completed', status_version=1, created_at=now, deadline_at=now + timedelta(minutes=1))
            evaluation = AlertEvaluation.objects.create(patient=patient, task_id=task.id, environment='test', policy=group['rule'],
                window_policy=group['window'], assessment_at=now, known_at=now, context_sequence=index + 1, mode='current', status='completed',
                decision='met', data_epoch=0, verification_epoch=0, publication_epoch=self.gate.publication_epoch, authorization_version=1,
                lease_generation=1, use_authorization_id=authorization.id, input_digest='b' * 64, missing_items=[], computed_at=now, validity='valid')
            episode = AlertEpisode.objects.create(patient=patient, rule_family_key=f'synthetic-family-{index}', status='open', opened_at=now,
                last_evaluation_id=evaluation.id, last_assessment_at=now, last_context_sequence=index + 1, state_version=1)
            event = AlertEvent.objects.create(patient=patient, evaluation_id=evaluation.id, release=release, trigger_key=str(uuid4()),
                trigger_policy=group['rule'], validity='valid', created_at=now, input_snapshot={'schema_version': 'dynamic-family-event-2',
                    'episode_id': str(episode.id), 'evaluation_id': str(evaluation.id), 'input_digest': 'b' * 64,
                    'context_sequence': str(index + 1), 'publication': group['rule'].payload['publication'],
                    'data_epoch': '0', 'verification_epoch': '0', 'publication_epoch': str(self.gate.publication_epoch)})
            episode.last_event_id = event.id; episode.save(update_fields=['last_event_id'])
            content = alert_content(event, ['temp'])
            self.assertEqual(content['content']['content_id'], str(group['content'].id))
            self.assertEqual(content['content']['approved_text'], group['content'].payload['approved_text'])
