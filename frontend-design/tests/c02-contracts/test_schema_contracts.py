"""Pure synthetic Schema cases and strict TypeScript fixtures; no services or patient data."""
import copy, importlib.util, json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
ROOT=Path(__file__).resolve().parents[3]
HERE=ROOT/'.planning-work/c02-contract-tests'
HERE.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('registry', ROOT/'backend-design/check_v2.py')
registry_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry_module)
file=ROOT/'backend-design/v2-protocol.schema.json'
registry,_,_=registry_module.local_schema_registry([file],root=ROOT)
defs=json.loads(file.read_text())['$defs']
u='00000000-0000-4000-8000-000000000001';stamp='2026-10-01T12:00:00Z';digest='a'*64
samples={
 'UploadGroupReceipt':dict(group_id=u,version='1',status='initiated'),
 'VerificationCreationQuery':dict(purpose='synthetic'),
 'VerificationOriginalQuery':dict(checksum=digest,object_version='1'),
 'VerificationRequestLookup':dict(lookup='request_id',operation_id='verification_v2_submit',target_key='/support/verifications'),
 'AssessmentCreationQuery':dict(known_at=stamp,mode='current'),
 'ClinicalDirectoryQuery':dict(action='verify_field'),
 'DynamicRequestLookup':dict(lookup='request_id',operation_id='alert_evaluation_v2_create',target_key='/support/patients/'+u+'/alert-evaluations'),
 'DynamicTriggerQuery':dict(cursor='synthetic-cursor'),
 'PageOriginalCopy':dict(copy_id=u,version='1',purpose='synthetic',status='pending_review',review_id=None,reviewed_at=None,revoked_at=None,identity_result=None,clinical_result=None),
 'DynamicSignal':dict(signal_id='synthetic',schema_version='alert-signal-1',algorithm_version='dynamic-2',state='met',reason_codes=[],input_field_ids=[],inputs=[],pair_results=[],assessment_kind='hlh'),
 'ClinicalMetricSummary':dict(detected_events=0,assessable_positive_events=0,positive_prompts=0,known_outcome_prompts=0,unknown_outcome_prompts=0,sensitivity=None,ppv=None,event_unassessable_fraction=None,alerts_per_observation_person_hour=None,alerts_per_negative_observation_person_hour=None,missed_event_ids=[],unassessable_positive_event_ids=[],unknown_event_ids=[],assessable_observation_person_hours='0',all_observation_person_hours='0',negative_observation_person_hours='0',prompt_counts=dict(total=0,first=0,repeat=0),first_alert_difference_seconds_by_event={},confidence_interval=None,confidence_interval_status='NOT_MEASURED_REQUIRES_APPROVED_PATIENT_CORRELATED_METHOD'),
 'RegroupRequestView':dict(intent_id=u,request_id=u,operation_id='archive_documents_regroup',target_key='/synthetic',prepared_at=stamp,state='pending',may_retry=False,receipt=None,error_code=None,generation='1',expires_at=stamp,payload_sha256=digest,regroup_input=dict(batch_id=u,sources=[dict(document_id=u,expected_version='1')],outputs=[],unassigned_page_ids=[],reason='synthetic')),
 'AssessmentObservationTime':dict(raw_text=None,precision='unknown',local_year=None,local_month=None,end_year=None,end_month=None,relative_text=None,local_date=None,local_datetime=None,timezone_name=None,utc_offset_minutes=None,instant=None,resolution_status='unknown'),
}
cases=[]
def add(name, value, valid, label, ts=True):
 v=Draft202012Validator({'$ref':file.as_uri()+'#/$defs/'+name},registry=registry,format_checker=FormatChecker())
 errors=list(v.iter_errors(value))
 assert bool(errors)!=valid,(name,label,[e.message for e in errors])
 cases.append(dict(name=name,value=value,valid=valid,label=label,ts=ts))
for name,sample in samples.items():
 add(name,sample,True,'baseline')
 for prop in defs[name].get('required',[]):
  changed=copy.deepcopy(sample);del changed[prop];add(name,changed,False,'missing_'+prop)
 changed=copy.deepcopy(sample);changed['unexpected']=True;add(name,changed,False,'extra_property')
 for prop in sample:
  changed=copy.deepcopy(sample);changed[prop]=False if sample[prop] is not False else 'false';add(name,changed,False,'wrong_type_'+prop)
 for prop,shape in defs[name].get('properties',{}).items():
  for value in shape.get('enum', [shape['const']] if 'const' in shape else []):
   changed=copy.deepcopy(sample);changed[prop]=value
   if name=='DynamicSignal' and prop=='state' and value=='unknown':changed['reason_codes']=['SYNTHETIC_UNKNOWN']
   add(name,changed,True,'allowed_'+prop+'_'+str(value))
  if 'enum' in shape or 'const' in shape:
   changed=copy.deepcopy(sample);changed[prop]='__INVALID_LITERAL__';add(name,changed,False,'bad_literal_'+prop)
add('DynamicTriggerQuery',{},True,'optional_cursor_absent')
add('DynamicTriggerQuery',{'cursor':None},False,'null_not_absent')
add('AssessmentObservationTime',{**samples['AssessmentObservationTime'],'id':u,'patient_id':u},True,'optional_storage_identity')
for prop in ['id','patient_id']:
 add('AssessmentObservationTime',{**samples['AssessmentObservationTime'],prop:None},False,'null_'+prop)
for prop in ['document_input','reminder_input','batch_input','disposition_input','batch_action_input','fact_input','dataset_input','evaluation_run_input','assessment_input','dynamic_input','verification_input']:
 add('RegroupRequestView',{**samples['RegroupRequestView'],prop:{}},False,'foreign_'+prop)
for name,change in [('VerificationCreationQuery',{'purpose':' '}),('VerificationOriginalQuery',{'checksum':'bad'}),('VerificationOriginalQuery',{'object_version':' '}),('UploadGroupReceipt',{'group_id':'bad'}),('UploadGroupReceipt',{'version':'0'}),('DynamicTriggerQuery',{'cursor':''})]:
 add(name,{**samples[name],**change},False,'runtime_format_'+next(iter(change)),False)
(HERE/'synthetic-contract-cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
names=[n for n in samples if n not in ['PageOriginalCopy','RegroupRequestView']]
lines=["import type { "+', '.join(names)+", PageOriginalView, AssessmentView, AssessmentViewWire } from '../../frontend-design/v2-types';", "import type { RegroupRequest as RegroupRequestView } from '../../frontend-design/archive-regroup-types';", "type PageOriginalCopy = PageOriginalView['purpose_copies'][number];"]
manifest=[]
for c in cases:
 if not c['ts']:continue
 name=f"case_{len(manifest)}";line='export const '+name+' = '+json.dumps(c['value'],ensure_ascii=False)+' satisfies '+c['name']+';'
 manifest.append(dict(id=name,valid=c['valid'],label=c['name']+'.'+c['label'],line=len(lines)+1));lines.append(line)
lines.extend(["declare const wire: AssessmentViewWire;", "export const rejects_unchecked_wire: AssessmentView = wire;"])
manifest.append(dict(id='rejects_unchecked_wire',valid=False,label='AssessmentView requires checked selections',line=len(lines)))
(HERE/'strict-contract-fixtures.ts').write_text('\n'.join(lines)+'\n')
(HERE/'strict-fixtures-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(HERE/'tsconfig.json').write_text(json.dumps({'compilerOptions':{'strict':True,'noEmit':True,'target':'ES2022','module':'ESNext','moduleResolution':'Bundler','skipLibCheck':True},'files':['strict-contract-fixtures.ts']},indent=2)+'\n')
print(json.dumps({'status':'PASS','schema_cases':len(cases),'schema_positive':sum(c['valid'] for c in cases),'schema_negative':sum(not c['valid'] for c in cases),'strict_ts_cases':len(manifest)},indent=2))
