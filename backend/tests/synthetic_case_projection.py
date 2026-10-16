"""Newly authored synthetic rows spanning all 18 case-domain tables."""
from copy import deepcopy
from uuid import uuid4


def complete_projection(minimal):
    value = deepcopy(minimal)
    ids = {key: str(uuid4()) for key in ('time','numeric','profile','episode','group','statement','study',
        'report','image','file','medication','medication_item','procedure','score','followup','followup_ref','issue','issue_ref')}
    source = value['fields'][0]
    patient, version, field = source['patient_id'], source['record_version_id'], source['id']
    value['medical_times'] = [{'id':ids['time'],'patient_id':patient,'raw_text':'SYNTHETIC 2026-01-02',
        'precision':'date','local_date':'2026-01-02','local_datetime':None,'timezone_name':None,
        'utc_offset_minutes':None,'instant':None,'resolution_status':'local_only','local_year':None,
        'local_month':None,'end_year':None,'end_month':None,'relative_text':None}]
    source['medical_time_id'] = ids['time']
    value['fields'].append({**source,'id':ids['numeric'],'original_name':'synthetic_numeric',
        'value_type':'decimal','raw_text':'1.2500 synthetic-unit','raw_numeric':'1.2500','qualifier':'eq','raw_unit':'synthetic-unit'})
    def context(key):
        return {'id':ids[key],'patient_id':patient,'record_version_id':version,'source_field_id':field}
    case = value['case_details']
    case['profiles']=[{**context('profile'),'sex':'unknown','reported_age_years':None,'reported_age_months':None,
        'age_raw_text':None,'age_reference_time_id':None,'birth_date':None,'patient_name':'SYNTHETIC ONLY','chief_complaint':'synthetic note'}]
    case['episodes']=[{**context('episode'),'episode_type':'other','label':'synthetic episode',
        'start_time_id':ids['time'],'end_time_id':None,'organization_text':'synthetic organization','department_text':None}]
    case['groups']=[{**context('group'),'group_type':'other','label':'synthetic group','specimen_text':None,
        'method_text':'synthetic method','medical_time_id':ids['time'],'source_quote':'synthetic quote',
        'members':[{'field_id':ids['numeric'],'ordinal':1,'role':'result'}]}]
    case['statements']=[{**context('statement'),'statement_type':'history','subject':'patient','category':'synthetic',
        'concept_code':None,'label':'synthetic statement','assertion_status':'unknown','status_raw_text':'synthetic unknown',
        'medical_time_id':ids['time'],'body_site':None,'laterality':None,
        'evidence':[{'evidence_field_id':field,'evidence_record_version_id':version,'role':'context'}]}]
    case['imaging_studies']=[{**context('study'),'report_id':ids['report'],'modality':'unknown','contrast':None,
        'study_time_id':ids['time'],'external_study_ref':'synthetic image',
        'sites':[{'ordinal':1,'body_site':'synthetic site','laterality':None}]}]
    case['image_links']=[{**context('image'),'file_id':ids['file'],'study_id':ids['study'],
        'link_status':'requires_confirmation','evidence_field_id':None,'evidence_record_version_id':None,
        'confirmed_by':None,'confirmed_at':None}]
    case['medication_events']=[{**context('medication'),'action':'regimen_record','medical_time_id':ids['time'],
        'previous_event_id':None,'reason_statement_id':ids['statement'],'note':'synthetic historical source',
        'items':[{'id':ids['medication_item'],'source_field_id':field,'ordinal':1,'medication_name':'synthetic item',
            'medication_code':None,'dose':'1.2500','dose_unit':'synthetic-unit','concentration_text':None,
            'route_text':'synthetic route','frequency_text':'synthetic frequency','interval_value':'2','interval_unit':'day'}]}]
    case['procedures']=[{**context('procedure'),'procedure_name':'synthetic procedure','medical_time_id':ids['time'],
        'related_medication_event_id':ids['medication'],'body_site':None}]
    case['scores']=[{**context('score'),'instrument':'synthetic source score','instrument_version':'test-1',
        'medical_time_id':ids['time'],'total_field_id':ids['numeric'],'context_text':'not a clinical calculation','note':None,
        'components':[{'field_id':ids['numeric'],'component_code':'synthetic component','ordinal':1}]}]
    case['followups']=[{**context('followup'),'kind':'latest_followup','label':'synthetic followup','medical_time_id':ids['time'],
        'channel_text':'synthetic channel','references':[{'id':ids['followup_ref'],'statement_id':None,
            'group_id':ids['group'],'score_id':None,'medication_event_id':None,'relation_kind':'laboratory'}]}]
    case['quality_issues']=[{**context('issue'),'category':'synthetic','description':'synthetic issue',
        'handling':'needs synthetic review','status':'open','resolution_text':None,'resolution_field_id':None,
        'resolution_record_version_id':None,'resolved_by':None,'resolved_at':None,
        'references':[{'id':ids['issue_ref'],'field_id':field,'record_version_id':version,'file_id':None,'role':'affected'}]}]
    return value, ids
