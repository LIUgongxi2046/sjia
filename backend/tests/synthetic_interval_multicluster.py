"""Two actual synthetic patients with a shared pre-frozen replay/CI contract.

All database identities and FK targets exist. The dataset/independent approval
boundary is an explicit software-test fixture, not the full purpose-copy/freeze
HTTP workflow. No qualification, replay, interval, save or read result is mocked.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import time

from django.utils import timezone
from alerting.models import AlertEvaluation
from alerting.service import create_dynamic_task, execute_dynamic_evaluation, read_dynamic_evaluation
from archives.models import EvaluationDatasetVersion, EvaluationPatientPartition, EvaluationLabel
from assessment.models import Assessment
from assessment.service import create_assessment_task, execute_static_assessment, read_assessment
from case_details.clinical_adoption import stable_result_digest
from case_details.clinical_metrics import Event, Prompt
from case_details.clinical_metrics_v2 import ClinicalCaptureV2, corpus_digest
from case_details.evaluation_datasets import dataset_checksum
from tasks.models import OutboxEvent
from tests.synthetic_clinical_capture import qualified_replay_capture


def qualified_multicluster_capture(case, interval_factory):
    prepared=[qualified_replay_capture(case,prepare_only=True)]
    prepared.append(qualified_replay_capture(case,prepare_only=True,shared_base=prepared[0].base,
        observation_padding_minutes=60))
    base=prepared[0].base;fx=base.fixture;purpose='clinical_evaluation'
    plan=deepcopy(prepared[0].plan)
    capture=deepcopy(prepared[0].capture)
    capture['patients']=[];capture['periods']=[];capture['events']=[];capture['configuration_checksums']={}
    plan['schedule']=[];plan['criteria_refs']=[]
    matching_ref=plan['matching_policy_ref']
    labels=[]
    for item in prepared:
        patient=item.base.patient
        item.prompt.update(id=f'synthetic-prompt-{patient.id}',outcome_ref=matching_ref)
        period=item.capture['periods'][0]
        period['reference_answer_ref']=matching_ref
        for mode in ('static','dynamic'):
            period['steps'][0][mode]['prompts']=[deepcopy(item.prompt)]
        event=item.capture['events'][0]
        event['reference_answer_ref']=matching_ref
        capture['patients'].extend(item.capture['patients'])
        capture['periods'].append(period);capture['events'].append(event)
        capture['configuration_checksums'].update(item.capture['configuration_checksums'])
        plan['schedule'].extend(item.plan['schedule']);plan['criteria_refs'].append(item.criteria_ref)
        labels.append({'label_id':str(item.label_id),'patient_id':str(patient.id),
            'observation_period_id':str(item.period.id),'label_state':'positive','reference_answer_ref':matching_ref})
    # An explicit, future fixed cutoff permits the real row checksum to be
    # known when the synthetic plan's corpus hashes are frozen. No evidence is
    # backdated; all artifacts are written before this cutoff, replay after it.
    frozen=max(timezone.now(),*(item.end.instant for item in prepared))+timedelta(seconds=5)
    row=EvaluationDatasetVersion(id=capture['dataset_version_id'],dataset_id=uuid4(),version=1,
        purpose=purpose,environment=fx.gate.environment,created_by=base.actor.id,created_at=timezone.now(),
        frozen_at=frozen,protocol_ref=plan['plan_ref'],status='frozen',
        coverage={'labels':labels,'synthetic_boundary':'independent frozen source/label fixture; no HTTP freeze claim'})
    row.checksum=dataset_checksum(row);row.save(force_insert=True)
    capture['dataset_checksum']=row.checksum
    plan['fixed_before']=timezone.now().isoformat()
    typed=ClinicalCaptureV2.model_validate(capture)
    for period,typed_period,point in zip(capture['periods'],typed.periods,plan['schedule']):
        checksum=corpus_digest(typed,typed_period,typed_period.steps[0])
        period['steps'][0]['corpus_sha256']=checksum
        point['corpus_sha256']=checksum
    interval=interval_factory(fx,plan)
    fx.evidence(plan,purposes=['evaluation_protocol',purpose],identity=plan['plan_ref'].rsplit(':',1)[1])
    matching={'schema_version':'clinical-explicit-pairings-2','measurement_plan_ref':plan['plan_ref'],
        'events':[Event.model_validate(event).model_dump(mode='json') for event in capture['events']],
        'periods':[{'id':period['id'],'subgroups':period['subgroups'],'steps':[{'id':step['id'],
            **{mode:[Prompt.model_validate(prompt).model_dump(mode='json') for prompt in step[mode]['prompts']]
                for mode in ('static','dynamic')}} for step in period['steps']]} for period in capture['periods']]}
    fx.evidence(matching,purposes=['evaluation_answer',purpose],identity=matching_ref.rsplit(':',1)[1])
    for item in prepared:
        patient=item.base.patient
        partition=EvaluationPatientPartition.objects.create(patient_id=patient.id,dataset_version_id=row.id,
            partition='development',use_authorization_id=item.base.authorization.id,source_refs=[{
                'source_revision_id':str(fx.source.id),'file_id':str(item.base.file.id)}])
        EvaluationLabel.objects.create(id=item.label_id,patient_id=patient.id,partition_id=partition.id,
            observation_period_id=item.period.id,label_state='positive',reference_answer_ref=matching_ref,
            reference_recognition_at=item.point,confirmed_by=base.actor.id,confirmed_at=timezone.now(),limitations=[])
    if timezone.now() >= frozen:
        raise AssertionError('Synthetic pre-freeze preparation exceeded its explicit fixture cutoff')
    time.sleep(max(0,(frozen-timezone.now()).total_seconds()))
    tasks=[]
    for item,period in zip(prepared,capture['periods']):
        patient=item.base.patient;patient.refresh_from_db()
        step=period['steps'][0]
        common={'mode':'replay','assessment_at':item.point.isoformat(),'known_at':item.point.isoformat(),
            'observation_period_id':str(item.period.id)}
        for mode in ('static','dynamic'):
            if mode=='static':
                task,_=create_assessment_task(patient_id=patient.id,actor=base.actor,auth_epoch=base.actor.auth_epoch,
                    environment=fx.gate.environment,payload={**common,'kind':'classification','rule_id':str(item.rule.id),
                        'window_policy_id':str(item.window.id),'verification_policy_id':str(item.base.configs['verification'].id)})
                execute_static_assessment(OutboxEvent.objects.get(task=task,event_type='assessment.requested'),'synthetic-ci-static')
                result=Assessment.objects.get(task_id=task.id)
                view=read_assessment(assessment_id=result.id,actor=base.actor,auth_epoch=base.actor.auth_epoch,environment=fx.gate.environment)
                reference=f'urn:sjia:assessment:{result.id}';status=view['computation_status']
                case.assertEqual(view['output']['classification_result'],'meets')
            else:
                task,_=create_dynamic_task(patient_id=patient.id,actor=base.actor,auth_epoch=base.actor.auth_epoch,
                    environment=fx.gate.environment,payload={**item.base.dynamic_payload,**common})
                execute_dynamic_evaluation(OutboxEvent.objects.get(task=task,event_type='alert_evaluation.requested'),'synthetic-ci-dynamic')
                result=AlertEvaluation.objects.get(task_id=task.id)
                view=read_dynamic_evaluation(evaluation_id=result.id,actor=base.actor,auth_epoch=base.actor.auth_epoch,environment=fx.gate.environment)
                reference=f'urn:sjia:alert-evaluation:{result.id}';status=view['status']
                case.assertEqual(view['decision'],'met')
            task.refresh_from_db()
            case.assertEqual((task.status,status,view['validity']),('completed','completed','valid'))
            selected={value['fact_version_id'] for value in task.input_refs['snapshot']['selected_refs'].values() if value is not None}
            if mode=='dynamic':
                selected.update(value['reference']['fact_version_id'] for signal in task.input_refs['snapshot']['signals'] for value in signal['inputs'])
            step[mode]={'execution_ref':reference,'task_id':str(task.id),'rule_config_id':str(item.rule.id if mode=='static' else item.base.configs['rule'].id),
                'execution_input_sha256':task.input_sha256,'result_sha256':stable_result_digest(view),'status':status,
                'used_fact_refs':sorted(selected),'prompts':[deepcopy(item.prompt)]}
            tasks.append(task)
    capture['captured_at']=timezone.now().isoformat()
    capture_source,capture_ref=fx.evidence(capture,purposes=['evaluation_capture',purpose])
    entries={'files':[SimpleNamespace(id=item.base.file.id,patient_id=item.base.patient.id) for item in prepared],
        'observation_periods':[item.period for item in prepared],
        'mas_events':[SimpleNamespace(id=item.event_id,patient_id=item.base.patient.id,
            reference_answer_ref=matching_ref,event_time_id=None) for item in prepared]}
    sources=SimpleNamespace(protocol=SimpleNamespace(evaluation_kind='clinical',measurement_plan_ref=plan['plan_ref'],criteria_refs=plan['criteria_refs']),
        manifest=SimpleNamespace(entries=lambda name:entries.get(name,[])))
    snapshot={'implementation_sha256':capture['implementation_sha256'],
        'configurations':[{'id':key,'sha256':value} for key,value in capture['configuration_checksums'].items()],
        'input':{'partition':'development'},'denominators':{'patient_ids':sorted(str(item.base.patient.id) for item in prepared),
            'unit_ids':{'observation_periods':sorted(str(item.period.id) for item in prepared)}}}
    return SimpleNamespace(base=base,row=row,sources=sources,snapshot=snapshot,capture=capture,plan=plan,
        capture_ref=capture_ref,capture_source=capture_source,tasks=tasks,interval=interval)
