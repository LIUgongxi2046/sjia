-- 与 schema.sql 依次执行；每条病例实体均关联不可变记录版本和原始字段。
SET search_path TO sjia, public;

CREATE TABLE patient_profile_version (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    sex text NOT NULL CHECK (sex IN ('male','female','other','unknown')),
    reported_age_years integer CHECK (reported_age_years >= 0),
    reported_age_months smallint CHECK (reported_age_months BETWEEN 0 AND 11),
    age_raw_text text,
    age_reference_time_id uuid,
    birth_date date,
    patient_name text,
    chief_complaint text,
    UNIQUE (id, patient_id),
    UNIQUE (record_version_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (age_reference_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    CHECK ((reported_age_years IS NULL) = (reported_age_months IS NULL)),
    CHECK (reported_age_years IS NULL OR age_raw_text IS NOT NULL)
);
ALTER TABLE patient ADD COLUMN current_profile_id uuid;
ALTER TABLE patient ADD CONSTRAINT patient_current_profile_fk
    FOREIGN KEY (current_profile_id, id) REFERENCES patient_profile_version(id, patient_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE clinical_episode (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    episode_type text NOT NULL CHECK (episode_type IN ('onset','hospitalization','icu','consultation','transfer','relapse','outpatient','other')),
    label text NOT NULL,
    start_time_id uuid,
    end_time_id uuid,
    organization_text text,
    department_text text,
    UNIQUE (id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (start_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (end_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE INDEX episode_patient_time ON clinical_episode(patient_id, start_time_id, id);

CREATE TABLE record_field_group (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    group_type text NOT NULL CHECK (group_type IN ('laboratory','vitals','microbiology','physical_exam','other')),
    label text NOT NULL,
    specimen_text text,
    method_text text,
    medical_time_id uuid,
    source_quote text,
    UNIQUE (id, patient_id),
    UNIQUE (id, record_version_id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE TABLE record_field_group_member (
    group_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    field_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    role text NOT NULL CHECK (role IN ('result','specimen','method','interpretation','context')),
    PRIMARY KEY (group_id, field_id),
    UNIQUE (group_id, ordinal),
    FOREIGN KEY (group_id, record_version_id, patient_id) REFERENCES record_field_group(id, record_version_id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id)
);
CREATE INDEX group_patient_kind ON record_field_group(patient_id, group_type, medical_time_id, id);

CREATE TABLE clinical_statement (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    statement_type text NOT NULL CHECK (statement_type IN ('history','physical_exam','diagnosis','differential','tolerance','symptom','laboratory_summary','followup_mode')),
    subject text NOT NULL CHECK (subject IN ('patient','family')),
    category text,
    concept_code text,
    label text NOT NULL,
    assertion_status text NOT NULL CHECK (assertion_status IN ('present','absent','normal','abnormal','source_diagnosis','historical_consideration','unsupported','requires_differentiation','unknown')),
    status_raw_text text NOT NULL,
    medical_time_id uuid,
    body_site text,
    laterality text CHECK (laterality IN ('left','right','bilateral','midline')),
    UNIQUE (id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE TABLE statement_evidence (
    statement_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    evidence_field_id uuid NOT NULL,
    evidence_record_version_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('supports','contradicts','context')),
    PRIMARY KEY (statement_id, evidence_field_id, role),
    FOREIGN KEY (statement_id, patient_id) REFERENCES clinical_statement(id, patient_id),
    FOREIGN KEY (evidence_field_id, evidence_record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id)
);
CREATE INDEX statement_patient_kind ON clinical_statement(patient_id, statement_type, medical_time_id, id);

CREATE TABLE imaging_study (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    report_id uuid NOT NULL,
    modality text NOT NULL CHECK (modality IN ('CT','MRI','ultrasound','radiograph','other','unknown')),
    contrast text CHECK (contrast IN ('noncontrast','enhanced')),
    study_time_id uuid,
    external_study_ref text,
    UNIQUE (id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (report_id, patient_id) REFERENCES report(id, patient_id),
    FOREIGN KEY (study_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE TABLE imaging_study_site (
    study_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    body_site text NOT NULL,
    laterality text CHECK (laterality IN ('left','right','bilateral','midline')),
    PRIMARY KEY (study_id, ordinal),
    FOREIGN KEY (study_id, patient_id) REFERENCES imaging_study(id, patient_id)
);
CREATE TABLE imaging_asset_link (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    file_id uuid NOT NULL,
    study_id uuid,
    link_status text NOT NULL CHECK (link_status IN ('requires_confirmation','confirmed','rejected')),
    evidence_field_id uuid,
    evidence_record_version_id uuid,
    confirmed_by uuid REFERENCES principal(id),
    confirmed_at timestamptz,
    UNIQUE (id, patient_id),
    UNIQUE (record_version_id, file_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (file_id, patient_id) REFERENCES file_object(id, patient_id),
    FOREIGN KEY (study_id, patient_id) REFERENCES imaging_study(id, patient_id),
    FOREIGN KEY (evidence_field_id, evidence_record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    CHECK ((evidence_field_id IS NULL) = (evidence_record_version_id IS NULL)),
    CHECK ((link_status = 'confirmed' AND study_id IS NOT NULL AND evidence_field_id IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)
      OR (link_status <> 'confirmed' AND confirmed_by IS NULL AND confirmed_at IS NULL)),
    CHECK (link_status <> 'requires_confirmation' OR study_id IS NULL)
);
CREATE INDEX imaging_patient_time ON imaging_study(patient_id, modality, study_time_id, id);

CREATE TABLE medication_event (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('historical_treatment','stopped_by_family','restart','stop','start','regimen_record','taper','dose_route_change','interval_change','ongoing')),
    medical_time_id uuid,
    previous_event_id uuid,
    reason_statement_id uuid,
    note text,
    UNIQUE (id, patient_id),
    UNIQUE (id, record_version_id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (previous_event_id, patient_id) REFERENCES medication_event(id, patient_id),
    FOREIGN KEY (reason_statement_id, patient_id) REFERENCES clinical_statement(id, patient_id),
    CHECK (previous_event_id IS NULL OR previous_event_id <> id)
);
CREATE TABLE medication_event_item (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    medication_name text NOT NULL,
    medication_code text,
    dose finite_decimal CHECK (dose > 0),
    dose_unit text,
    concentration_text text,
    route_text text,
    frequency_text text,
    interval_value finite_decimal CHECK (interval_value > 0),
    interval_unit text CHECK (interval_unit IN ('hour','day','week','month')),
    UNIQUE (id, patient_id),
    UNIQUE (event_id, ordinal),
    FOREIGN KEY (event_id, record_version_id, patient_id) REFERENCES medication_event(id, record_version_id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    CHECK ((dose IS NULL) = (dose_unit IS NULL)),
    CHECK ((interval_value IS NULL) = (interval_unit IS NULL)),
    CHECK (interval_value IS NULL OR frequency_text IS NOT NULL)
);
CREATE INDEX medication_patient_time ON medication_event(patient_id, medical_time_id, id);
CREATE INDEX medication_item_lookup ON medication_event_item(patient_id, medication_name, event_id);
CREATE TABLE procedure_event (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    procedure_name text NOT NULL,
    medical_time_id uuid,
    related_medication_event_id uuid,
    body_site text,
    UNIQUE (id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (related_medication_event_id, patient_id) REFERENCES medication_event(id, patient_id)
);

CREATE TABLE external_score_observation (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    instrument text NOT NULL,
    instrument_version text,
    medical_time_id uuid NOT NULL,
    total_field_id uuid NOT NULL,
    context_text text,
    note text,
    UNIQUE (id, patient_id),
    UNIQUE (id, record_version_id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (total_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE TABLE external_score_component (
    score_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    field_id uuid NOT NULL,
    component_code text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (score_id, component_code),
    UNIQUE (score_id, ordinal),
    FOREIGN KEY (score_id, record_version_id, patient_id) REFERENCES external_score_observation(id, record_version_id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id)
);
CREATE INDEX external_score_patient_time ON external_score_observation(patient_id, instrument, medical_time_id, id);

CREATE TABLE followup_encounter (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('baseline_assessment','treatment_assessment','followup_assessment','treatment_response','latest_followup')),
    label text NOT NULL,
    medical_time_id uuid NOT NULL,
    channel_text text,
    UNIQUE (id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE TABLE followup_reference (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    encounter_id uuid NOT NULL,
    statement_id uuid,
    group_id uuid,
    score_id uuid,
    medication_event_id uuid,
    relation_kind text NOT NULL CHECK (relation_kind IN ('outcome','laboratory','score','treatment_context','reported_current_treatment')),
    FOREIGN KEY (encounter_id, patient_id) REFERENCES followup_encounter(id, patient_id),
    FOREIGN KEY (statement_id, patient_id) REFERENCES clinical_statement(id, patient_id),
    FOREIGN KEY (group_id, patient_id) REFERENCES record_field_group(id, patient_id),
    FOREIGN KEY (score_id, patient_id) REFERENCES external_score_observation(id, patient_id),
    FOREIGN KEY (medication_event_id, patient_id) REFERENCES medication_event(id, patient_id),
    CHECK (num_nonnulls(statement_id, group_id, score_id, medication_event_id) = 1),
    CHECK ((relation_kind = 'outcome' AND statement_id IS NOT NULL)
        OR (relation_kind = 'laboratory' AND group_id IS NOT NULL)
        OR (relation_kind = 'score' AND score_id IS NOT NULL)
        OR (relation_kind IN ('treatment_context','reported_current_treatment') AND medication_event_id IS NOT NULL)),
    UNIQUE NULLS NOT DISTINCT (encounter_id, statement_id, group_id, score_id, medication_event_id, relation_kind)
);
CREATE INDEX followup_patient_time ON followup_encounter(patient_id, medical_time_id, id);

CREATE TABLE data_quality_issue (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    source_field_id uuid NOT NULL,
    category text NOT NULL,
    description text NOT NULL,
    handling text NOT NULL,
    status text NOT NULL CHECK (status IN ('open','needs_review','resolved')),
    resolution_text text,
    resolution_field_id uuid,
    resolution_record_version_id uuid,
    resolved_by uuid REFERENCES principal(id),
    resolved_at timestamptz,
    UNIQUE (id, patient_id),
    FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (resolution_field_id, resolution_record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    CHECK ((resolution_field_id IS NULL) = (resolution_record_version_id IS NULL)),
    CHECK ((status = 'resolved' AND resolution_text IS NOT NULL AND resolution_field_id IS NOT NULL AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
      OR (status <> 'resolved' AND resolution_text IS NULL AND resolution_field_id IS NULL AND resolved_by IS NULL AND resolved_at IS NULL))
);
CREATE TABLE data_quality_issue_reference (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    issue_id uuid NOT NULL,
    field_id uuid,
    record_version_id uuid,
    file_id uuid,
    role text NOT NULL CHECK (role IN ('source','comparison','affected')),
    FOREIGN KEY (issue_id, patient_id) REFERENCES data_quality_issue(id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (file_id, patient_id) REFERENCES file_object(id, patient_id),
    CHECK ((field_id IS NULL) = (record_version_id IS NULL)),
    CHECK (num_nonnulls(field_id, file_id) = 1),
    UNIQUE NULLS NOT DISTINCT (issue_id, field_id, file_id, role)
);
CREATE INDEX quality_patient_status ON data_quality_issue(patient_id, status, id);

-- 当前版本视图仅提供内部查询候选；应用账号仍需领域授权和可见范围投影。
CREATE VIEW current_case_record_version AS
SELECT rv.* FROM record_version rv
JOIN record r ON (r.id, r.patient_id, r.current_version) = (rv.record_id, rv.patient_id, rv.version)
JOIN patient p ON p.id = rv.patient_id
WHERE r.status = 'active' AND p.status = 'active';

REVOKE ALL ON ALL TABLES IN SCHEMA sjia FROM PUBLIC;
