-- 数据结构设计；生产实施通过 Django migrations 执行。
CREATE SCHEMA sjia;
SET search_path TO sjia, public;

CREATE DOMAIN finite_decimal AS numeric
    CHECK (VALUE NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
CREATE DOMAIN sha256_hex AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE principal (
    id uuid PRIMARY KEY,
    account_status text NOT NULL CHECK (account_status IN ('active','disabled','closing','closed')),
    auth_epoch bigint NOT NULL DEFAULT 0 CHECK (auth_epoch >= 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE TABLE principal_identity (
    id uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES principal(id),
    provider text NOT NULL,
    provider_subject text NOT NULL,
    verified_at timestamptz NOT NULL,
    UNIQUE (provider, provider_subject)
);
CREATE TABLE patient (
    id uuid PRIMARY KEY,
    display_id text NOT NULL UNIQUE,
    data_epoch bigint NOT NULL DEFAULT 0 CHECK (data_epoch >= 0),
    verification_epoch bigint NOT NULL DEFAULT 0 CHECK (verification_epoch >= 0),
    evaluation_sequence bigint NOT NULL DEFAULT 0 CHECK (evaluation_sequence >= 0),
    status text NOT NULL CHECK (status IN ('active','restricted','disposed')),
    created_at timestamptz NOT NULL
);
CREATE TABLE retention_policy (
    id uuid PRIMARY KEY,
    policy_key text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    data_class text NOT NULL,
    rules jsonb NOT NULL CHECK (jsonb_typeof(rules) = 'object'),
    schema_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('draft','approved','active','disabled')),
    approved_by uuid REFERENCES principal(id),
    approved_at timestamptz,
    evidence_ref text,
    UNIQUE (policy_key, version),
    CHECK (status IN ('draft','disabled') OR
      (approved_by IS NOT NULL AND approved_at IS NOT NULL AND evidence_ref IS NOT NULL))
);
CREATE TABLE source_registry (
    id uuid PRIMARY KEY,
    source_key text NOT NULL UNIQUE,
    owner text NOT NULL,
    location text NOT NULL,
    purpose text NOT NULL,
    sensitivity text NOT NULL,
    update_frequency text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','approved','disabled'))
);
CREATE TABLE source_revision (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES source_registry(id),
    version text NOT NULL,
    checksum sha256_hex NOT NULL,
    license text NOT NULL,
    allowed_use jsonb NOT NULL CHECK (jsonb_typeof(allowed_use) = 'array'),
    rights_evidence_ref text,
    retention_policy_id uuid REFERENCES retention_policy(id),
    captured_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','approved','revoked')),
    UNIQUE (source_id, version)
);
CREATE TABLE data_use_authorization (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    purpose text NOT NULL,
    environment text NOT NULL,
    evidence_ref text NOT NULL,
    deidentification_ref text,
    approved_by uuid NOT NULL REFERENCES principal(id),
    approved_at timestamptz NOT NULL,
    valid_until timestamptz,
    revoked_at timestamptz,
    UNIQUE (id, patient_id)
);
CREATE TABLE config_version (
    id uuid PRIMARY KEY,
    config_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('dictionary','mapping','verification','window','rule','visibility','content','summary','notification','provider','validation')),
    version bigint NOT NULL CHECK (version > 0),
    schema_version text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    checksum sha256_hex NOT NULL,
    source_revision_id uuid REFERENCES source_revision(id),
    status text NOT NULL CHECK (status IN ('draft','pending','approved','enabled','disabled')),
    created_by uuid NOT NULL REFERENCES principal(id),
    created_at timestamptz NOT NULL,
    UNIQUE (config_key, version)
);
CREATE TABLE config_approval (
    id uuid PRIMARY KEY,
    config_id uuid NOT NULL REFERENCES config_version(id),
    approved_by uuid NOT NULL REFERENCES principal(id),
    approval_role text NOT NULL,
    approved_at timestamptz NOT NULL,
    evidence_ref text NOT NULL,
    effective_scope jsonb NOT NULL CHECK (jsonb_typeof(effective_scope) = 'object'),
    valid_until timestamptz,
    revoked_at timestamptz,
    revocation_reason text,
    CHECK (revoked_at IS NULL OR revocation_reason IS NOT NULL)
);
CREATE TABLE relation_application (
    id uuid PRIMARY KEY,
    version bigint NOT NULL CHECK (version > 0),
    applicant_id uuid NOT NULL REFERENCES principal(id),
    patient_id uuid REFERENCES patient(id),
    evidence_ref text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn')),
    checked_by uuid REFERENCES principal(id),
    checked_at timestamptz,
    reason text,
    created_at timestamptz NOT NULL,
    CHECK (status NOT IN ('approved','rejected') OR (checked_by IS NOT NULL AND checked_at IS NOT NULL))
);
CREATE TABLE relation (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    guardian_id uuid NOT NULL REFERENCES principal(id),
    application_id uuid NOT NULL REFERENCES relation_application(id),
    status text NOT NULL CHECK (status IN ('active','revoked','expired')),
    version bigint NOT NULL CHECK (version > 0),
    auth_epoch bigint NOT NULL DEFAULT 0 CHECK (auth_epoch >= 0),
    checked_by uuid NOT NULL REFERENCES principal(id),
    checked_at timestamptz NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_until timestamptz,
    revoked_at timestamptz,
    reason text,
    UNIQUE (id, patient_id),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (status <> 'revoked' OR (revoked_at IS NOT NULL AND reason IS NOT NULL))
);
CREATE UNIQUE INDEX relation_one_active ON relation(patient_id, guardian_id) WHERE status = 'active';
CREATE TABLE access_grant (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    principal_id uuid NOT NULL REFERENCES principal(id),
    relation_id uuid,
    action text NOT NULL,
    field_codes text[] NOT NULL,
    record_scope text NOT NULL CHECK (record_scope IN ('own','all','assigned')),
    assigned_record_id uuid,
    purpose text NOT NULL,
    granted_by uuid NOT NULL REFERENCES principal(id),
    evidence_ref text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_until timestamptz,
    revoked_at timestamptz,
    FOREIGN KEY (relation_id, patient_id) REFERENCES relation(id, patient_id),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK ((record_scope = 'assigned') = (assigned_record_id IS NOT NULL))
);
CREATE INDEX access_lookup ON access_grant(principal_id, patient_id, action);

CREATE TABLE dictionary_entry (
    id uuid PRIMARY KEY,
    dictionary_version_id uuid NOT NULL REFERENCES config_version(id),
    concept_code text NOT NULL,
    category text NOT NULL,
    label text NOT NULL,
    definition_ref text NOT NULL,
    UNIQUE (dictionary_version_id, concept_code),
    UNIQUE (id, dictionary_version_id)
);
CREATE TABLE dictionary_alias (
    entry_id uuid NOT NULL,
    dictionary_version_id uuid NOT NULL,
    original_alias text NOT NULL,
    normalized_alias text NOT NULL,
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    PRIMARY KEY (entry_id, normalized_alias),
    FOREIGN KEY (entry_id, dictionary_version_id) REFERENCES dictionary_entry(id, dictionary_version_id)
);
CREATE INDEX dictionary_alias_lookup ON dictionary_alias(dictionary_version_id, normalized_alias);
CREATE TABLE unit_mapping (
    id uuid PRIMARY KEY,
    config_id uuid NOT NULL REFERENCES config_version(id),
    concept_code text NOT NULL,
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    raw_unit text NOT NULL,
    normalized_unit text NOT NULL,
    method_scope text NOT NULL,
    multiplier finite_decimal NOT NULL,
    additive finite_decimal NOT NULL,
    exact_values_only boolean NOT NULL,
    UNIQUE (config_id, concept_code, source_revision_id, raw_unit, method_scope)
);
CREATE TABLE write_request (
    id uuid PRIMARY KEY,
    actor_id uuid NOT NULL REFERENCES principal(id),
    patient_id uuid REFERENCES patient(id),
    action text NOT NULL,
    target_key text NOT NULL,
    request_id uuid NOT NULL,
    payload_sha256 sha256_hex NOT NULL,
    normalization_version text NOT NULL,
    expected_version bigint,
    status text NOT NULL CHECK (status IN ('processing','committed','failed','uncertain')),
    resource_type text,
    resource_id uuid,
    error_code text,
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    expires_at timestamptz,
    UNIQUE (actor_id, action, target_key, request_id),
    CHECK (status <> 'committed' OR (resource_type IS NOT NULL AND resource_id IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE TABLE draft (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    owner_id uuid NOT NULL REFERENCES principal(id),
    form_type text NOT NULL CHECK (form_type IN ('observation','report','therapy','correction')),
    version bigint NOT NULL CHECK (version > 0),
    schema_version text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    correction_record_id uuid,
    correction_version bigint,
    status text NOT NULL CHECK (status IN ('editing','submitted','abandoned','expired')),
    submitted_request_id uuid REFERENCES write_request(id),
    saved_at timestamptz NOT NULL,
    expires_at timestamptz,
    retention_policy_id uuid NOT NULL REFERENCES retention_policy(id),
    UNIQUE (id, patient_id),
    CHECK ((form_type = 'correction' AND correction_record_id IS NOT NULL AND correction_version IS NOT NULL)
        OR (form_type <> 'correction' AND correction_record_id IS NULL AND correction_version IS NULL)),
    CHECK (status <> 'submitted' OR submitted_request_id IS NOT NULL)
);
CREATE INDEX draft_lookup ON draft(owner_id, patient_id, form_type, status, saved_at DESC);

CREATE TABLE file_group (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    owner_id uuid NOT NULL REFERENCES principal(id),
    draft_id uuid,
    version bigint NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('initiated','uploaded','validating','available','failed','superseded','disposed')),
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    use_authorization_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    FOREIGN KEY (draft_id, patient_id) REFERENCES draft(id, patient_id),
    FOREIGN KEY (use_authorization_id, patient_id) REFERENCES data_use_authorization(id, patient_id)
);
CREATE TABLE file_object (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    file_group_id uuid NOT NULL,
    derived_from_id uuid,
    object_key text NOT NULL,
    object_version text NOT NULL,
    checksum sha256_hex NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    media_type text NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('original','thumbnail','deidentified','ocr_response')),
    processing_version text,
    status text NOT NULL CHECK (status IN ('quarantined','available','failed','disposed')),
    retention_policy_id uuid NOT NULL REFERENCES retention_policy(id),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    UNIQUE (object_key, object_version),
    FOREIGN KEY (file_group_id, patient_id) REFERENCES file_group(id, patient_id),
    FOREIGN KEY (derived_from_id, patient_id) REFERENCES file_object(id, patient_id)
);
CREATE TABLE file_page (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    file_group_id uuid NOT NULL,
    file_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    width integer NOT NULL CHECK (width > 0),
    height integer NOT NULL CHECK (height > 0),
    rotation integer NOT NULL CHECK (rotation IN (0,90,180,270)),
    UNIQUE (id, patient_id),
    UNIQUE (file_group_id, ordinal),
    FOREIGN KEY (file_group_id, patient_id) REFERENCES file_group(id, patient_id),
    FOREIGN KEY (file_id, patient_id) REFERENCES file_object(id, patient_id)
);
CREATE TABLE candidate_set (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    file_group_id uuid NOT NULL,
    file_group_version bigint NOT NULL,
    task_id uuid NOT NULL,
    provider text NOT NULL,
    model_version text NOT NULL,
    provider_request_id text NOT NULL,
    schema_version text NOT NULL,
    raw_response_file_id uuid,
    status text NOT NULL CHECK (status IN ('pending_confirmation','confirmed','superseded','cancelled')),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    FOREIGN KEY (file_group_id, patient_id) REFERENCES file_group(id, patient_id),
    FOREIGN KEY (raw_response_file_id, patient_id) REFERENCES file_object(id, patient_id)
);
CREATE TABLE source_anchor (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    page_id uuid NOT NULL,
    anchor_kind text NOT NULL CHECK (anchor_kind IN ('page','bbox')),
    coordinate_system text NOT NULL CHECK (coordinate_system = 'decoded_pixel'),
    x finite_decimal,
    y finite_decimal,
    width finite_decimal,
    height finite_decimal,
    row_label text,
    column_label text,
    original_text text NOT NULL,
    UNIQUE (id, patient_id),
    FOREIGN KEY (page_id, patient_id) REFERENCES file_page(id, patient_id),
    CHECK ((anchor_kind = 'page' AND x IS NULL AND y IS NULL AND width IS NULL AND height IS NULL)
       OR (anchor_kind = 'bbox' AND x IS NOT NULL AND y IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL
           AND x >= 0 AND y >= 0 AND width > 0 AND height > 0))
);
CREATE TABLE candidate_field (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    candidate_set_id uuid NOT NULL,
    field_path text NOT NULL,
    raw_text text,
    field_config_version uuid NOT NULL REFERENCES config_version(id),
    proposed_field jsonb CHECK (jsonb_typeof(proposed_field) = 'object'),
    anchor_id uuid,
    recognition_status text NOT NULL CHECK (recognition_status IN ('recognized','uncertain','missing','unlocated')),
    confidence finite_decimal CHECK (confidence BETWEEN 0 AND 1),
    UNIQUE (id, patient_id),
    UNIQUE (candidate_set_id, field_path),
    FOREIGN KEY (candidate_set_id, patient_id) REFERENCES candidate_set(id, patient_id),
    FOREIGN KEY (anchor_id, patient_id) REFERENCES source_anchor(id, patient_id)
);
CREATE TABLE candidate_confirmation (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    candidate_field_id uuid NOT NULL,
    confirmed_by uuid NOT NULL REFERENCES principal(id),
    confirmed_text text,
    decision text NOT NULL CHECK (decision IN ('accepted','modified','unknown','excluded')),
    confirmed_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    FOREIGN KEY (candidate_field_id, patient_id) REFERENCES candidate_field(id, patient_id)
);

CREATE TABLE medical_time (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    raw_text text,
    precision text NOT NULL CHECK (precision IN ('unknown','year','month','month_range','relative','date','minute','second')),
    local_year integer CHECK (local_year BETWEEN 1 AND 9999),
    local_month smallint CHECK (local_month BETWEEN 1 AND 12),
    end_year integer CHECK (end_year BETWEEN 1 AND 9999),
    end_month smallint CHECK (end_month BETWEEN 1 AND 12),
    relative_text text,
    local_date date,
    local_datetime timestamp,
    timezone_name text,
    utc_offset_minutes smallint CHECK (utc_offset_minutes BETWEEN -840 AND 840),
    instant timestamptz,
    resolution_status text NOT NULL CHECK (resolution_status IN ('unknown','local_only','resolved','ambiguous','invalid')),
    UNIQUE (id, patient_id),
    CHECK (precision <> 'date' OR (local_date IS NOT NULL AND local_datetime IS NULL AND instant IS NULL)),
    CHECK (precision <> 'unknown' OR (local_date IS NULL AND local_datetime IS NULL AND instant IS NULL)),
    CHECK (precision NOT IN ('minute','second') OR local_datetime IS NOT NULL),
    CHECK (instant IS NULL OR (precision IN ('minute','second') AND local_datetime IS NOT NULL
           AND timezone_name IS NOT NULL AND utc_offset_minutes IS NOT NULL AND resolution_status = 'resolved')),
    CHECK (resolution_status <> 'resolved' OR instant IS NOT NULL),
    CHECK (precision <> 'unknown' OR resolution_status = 'unknown'),
    CHECK (precision <> 'date' OR resolution_status = 'local_only'),
    CONSTRAINT medical_time_partial_shape CHECK (
      (precision = 'year' AND local_year IS NOT NULL AND local_month IS NULL AND end_year IS NULL AND end_month IS NULL AND relative_text IS NULL)
      OR (precision = 'month' AND local_year IS NOT NULL AND local_month IS NOT NULL AND end_year IS NULL AND end_month IS NULL AND relative_text IS NULL)
      OR (precision = 'month_range' AND local_year IS NOT NULL AND local_month IS NOT NULL AND end_year IS NOT NULL AND end_month IS NOT NULL AND relative_text IS NULL
          AND (local_year, local_month) <= (end_year, end_month))
      OR (precision = 'relative' AND relative_text IS NOT NULL AND length(trim(relative_text)) > 0 AND local_year IS NULL AND local_month IS NULL AND end_year IS NULL AND end_month IS NULL)
      OR (precision IN ('unknown','date','minute','second') AND local_year IS NULL AND local_month IS NULL AND end_year IS NULL AND end_month IS NULL AND relative_text IS NULL)
    ),
    CHECK (precision NOT IN ('year','month','month_range','relative') OR
      (local_date IS NULL AND local_datetime IS NULL AND instant IS NULL AND timezone_name IS NULL AND utc_offset_minutes IS NULL)),
    CHECK (precision NOT IN ('year','month','month_range') OR resolution_status = 'local_only'),
    CHECK (precision <> 'relative' OR resolution_status = 'unknown')
);
CREATE TABLE report (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    source_revision_id uuid REFERENCES source_revision(id),
    external_ref text,
    source_organization text,
    report_status text NOT NULL CHECK (report_status IN ('unknown','preliminary','final','corrected')),
    sample_time_id uuid,
    result_time_id uuid,
    file_group_id uuid,
    replaces_report_id uuid,
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    FOREIGN KEY (sample_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (result_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (file_group_id, patient_id) REFERENCES file_group(id, patient_id),
    FOREIGN KEY (replaces_report_id, patient_id) REFERENCES report(id, patient_id)
);
CREATE TABLE record (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    kind text NOT NULL CHECK (kind IN ('observation','report','therapy','professional')),
    current_version bigint NOT NULL CHECK (current_version > 0),
    status text NOT NULL CHECK (status IN ('active','withdrawn','disposed')),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id)
);
CREATE TABLE record_version (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    record_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    supersedes_version bigint,
    author_id uuid NOT NULL REFERENCES principal(id),
    origin_type text NOT NULL CHECK (origin_type IN ('self_report','report_transcription','authorized_clinical')),
    capture_method text NOT NULL CHECK (capture_method IN ('manual','ocr_confirmed','voice_confirmed','authorized_import')),
    source_revision_id uuid REFERENCES source_revision(id),
    source_organization text,
    report_id uuid,
    use_authorization_id uuid NOT NULL,
    quality_status text NOT NULL CHECK (quality_status IN ('pending','valid','needs_correction')),
    quality_reasons jsonb NOT NULL CHECK (jsonb_typeof(quality_reasons) = 'array'),
    correction_reason text,
    corrected_at timestamptz,
    recorded_at timestamptz NOT NULL,
    retention_policy_id uuid NOT NULL REFERENCES retention_policy(id),
    UNIQUE (id, patient_id),
    UNIQUE (record_id, patient_id, version),
    FOREIGN KEY (record_id, patient_id) REFERENCES record(id, patient_id),
    FOREIGN KEY (record_id, patient_id, supersedes_version) REFERENCES record_version(record_id, patient_id, version),
    FOREIGN KEY (report_id, patient_id) REFERENCES report(id, patient_id),
    FOREIGN KEY (use_authorization_id, patient_id) REFERENCES data_use_authorization(id, patient_id),
    CHECK ((version = 1 AND supersedes_version IS NULL AND correction_reason IS NULL AND corrected_at IS NULL)
        OR (version > 1 AND supersedes_version IS NOT NULL AND supersedes_version = version - 1 AND correction_reason IS NOT NULL AND corrected_at IS NOT NULL))
);
ALTER TABLE record ADD CONSTRAINT record_current_version_fk
    FOREIGN KEY (id, patient_id, current_version) REFERENCES record_version(record_id, patient_id, version)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE access_grant ADD CONSTRAINT grant_record_fk
    FOREIGN KEY (assigned_record_id, patient_id) REFERENCES record(id, patient_id);
ALTER TABLE draft ADD CONSTRAINT draft_correction_fk
    FOREIGN KEY (correction_record_id, patient_id, correction_version) REFERENCES record_version(record_id, patient_id, version);
CREATE TABLE record_attachment (
    record_version_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    file_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (record_version_id, ordinal),
    UNIQUE (record_version_id, file_id),
    FOREIGN KEY (record_version_id, patient_id) REFERENCES record_version(id, patient_id),
    FOREIGN KEY (file_id, patient_id) REFERENCES file_object(id, patient_id)
);
CREATE TABLE record_field (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    record_version_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    field_code text NOT NULL,
    concept_code text,
    dictionary_entry_id uuid REFERENCES dictionary_entry(id),
    original_name text,
    value_type text NOT NULL CHECK (value_type IN ('decimal','categorical','text','time')),
    value_state text NOT NULL CHECK (value_state IN ('known','unknown','invalid')),
    raw_text text,
    raw_numeric finite_decimal,
    categorical_value text,
    qualifier text CHECK (qualifier IN ('eq','lt','le','gt','ge')),
    raw_unit text,
    method text,
    reference_interval text,
    report_flag text,
    medical_time_id uuid,
    sample_time_origin text NOT NULL CHECK (sample_time_origin IN ('report','item','observation','therapy','unknown')),
    anchor_id uuid,
    confirmation_id uuid,
    quality_reasons jsonb NOT NULL CHECK (jsonb_typeof(quality_reasons) = 'array'),
    UNIQUE (id, patient_id),
    UNIQUE (id, record_version_id, patient_id),
    UNIQUE (record_version_id, ordinal),
    FOREIGN KEY (record_version_id, patient_id) REFERENCES record_version(id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (anchor_id, patient_id) REFERENCES source_anchor(id, patient_id),
    FOREIGN KEY (confirmation_id, patient_id) REFERENCES candidate_confirmation(id, patient_id),
    CHECK (value_state = 'known' OR (raw_numeric IS NULL AND categorical_value IS NULL)),
    CHECK (value_type = 'decimal' OR raw_numeric IS NULL),
    CHECK (value_type = 'categorical' OR categorical_value IS NULL),
    CHECK (value_state <> 'known' OR
        (value_type = 'decimal' AND raw_numeric IS NOT NULL) OR
        (value_type = 'categorical' AND categorical_value IS NOT NULL) OR
        (value_type = 'text' AND raw_text IS NOT NULL) OR
        (value_type = 'time' AND medical_time_id IS NOT NULL))
);
CREATE INDEX record_patient_kind ON record(patient_id, kind, created_at DESC, id);
CREATE INDEX field_patient_concept ON record_field(patient_id, concept_code, medical_time_id);
CREATE INDEX version_recorded ON record_version(patient_id, recorded_at DESC, id);
CREATE INDEX time_ordering ON medical_time(patient_id, precision, instant DESC, id);
CREATE TABLE verification (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    record_version_id uuid NOT NULL,
    verifier_id uuid NOT NULL REFERENCES principal(id),
    policy_id uuid NOT NULL REFERENCES config_version(id),
    purpose text NOT NULL,
    evidence_ref text NOT NULL,
    evidence_source_id uuid NOT NULL REFERENCES source_revision(id),
    verified_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revoked_by uuid REFERENCES principal(id),
    revocation_reason text,
    UNIQUE (id, record_version_id, patient_id),
    FOREIGN KEY (record_version_id, patient_id) REFERENCES record_version(id, patient_id),
    CHECK (revoked_at IS NULL OR (revoked_by IS NOT NULL AND revocation_reason IS NOT NULL))
);
CREATE TABLE verification_field (
    verification_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    field_id uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('consistent','needs_correction','uncertain')),
    reason text,
    PRIMARY KEY (verification_id, field_id),
    FOREIGN KEY (verification_id, record_version_id, patient_id) REFERENCES verification(id, record_version_id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id)
);
CREATE INDEX verification_field_reverse ON verification_field(field_id, verification_id);
CREATE TABLE verification_request (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    requested_by uuid NOT NULL REFERENCES principal(id),
    assigned_to uuid REFERENCES principal(id),
    purpose text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','in_review','completed','cancelled','superseded')),
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (id, record_version_id, patient_id),
    FOREIGN KEY (record_version_id, patient_id) REFERENCES record_version(id, patient_id)
);
CREATE TABLE field_verification_state (
    field_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    purpose text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    request_id uuid,
    current_verification_id uuid,
    status text NOT NULL CHECK (status IN ('unverified','pending','verified','needs_correction','revoked')),
    PRIMARY KEY (field_id, purpose),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (request_id, record_version_id, patient_id) REFERENCES verification_request(id, record_version_id, patient_id),
    FOREIGN KEY (current_verification_id, record_version_id, patient_id) REFERENCES verification(id, record_version_id, patient_id),
    FOREIGN KEY (current_verification_id, field_id) REFERENCES verification_field(verification_id, field_id),
    CHECK (status <> 'verified' OR current_verification_id IS NOT NULL),
    CHECK (status <> 'pending' OR request_id IS NOT NULL)
);
CREATE TABLE normalized_measurement (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    field_id uuid NOT NULL,
    mapping_id uuid NOT NULL REFERENCES unit_mapping(id),
    normalized_value finite_decimal NOT NULL,
    normalized_unit text NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (id, field_id, patient_id),
    UNIQUE (field_id, mapping_id),
    FOREIGN KEY (field_id, patient_id) REFERENCES record_field(id, patient_id)
);

CREATE TABLE assessment (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    kind text NOT NULL CHECK (kind IN ('classification','ms')),
    assessment_at timestamptz,
    rule_id uuid REFERENCES config_version(id),
    window_policy_id uuid REFERENCES config_version(id),
    task_id uuid NOT NULL,
    data_epoch bigint NOT NULL,
    verification_epoch bigint NOT NULL,
    computation_status text NOT NULL CHECK (computation_status IN ('not_computed','configuration_pending','insufficient','completed','failed')),
    validity text NOT NULL CHECK (validity IN ('valid','invalid')),
    missing_items jsonb NOT NULL CHECK (jsonb_typeof(missing_items) = 'array'),
    computed_at timestamptz,
    UNIQUE (id, patient_id),
    CHECK (computation_status <> 'completed' OR (rule_id IS NOT NULL AND window_policy_id IS NOT NULL AND assessment_at IS NOT NULL AND computed_at IS NOT NULL))
);
CREATE TABLE assessment_input (
    assessment_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    variable_code text NOT NULL,
    record_version_id uuid NOT NULL,
    field_id uuid NOT NULL,
    verification_id uuid NOT NULL,
    normalization_id uuid,
    input_snapshot jsonb NOT NULL CHECK (jsonb_typeof(input_snapshot) = 'object'),
    PRIMARY KEY (assessment_id, variable_code),
    FOREIGN KEY (assessment_id, patient_id) REFERENCES assessment(id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (verification_id, record_version_id, patient_id) REFERENCES verification(id, record_version_id, patient_id),
    FOREIGN KEY (verification_id, field_id) REFERENCES verification_field(verification_id, field_id),
    FOREIGN KEY (normalization_id, field_id, patient_id) REFERENCES normalized_measurement(id, field_id, patient_id)
);
CREATE TABLE assessment_output (
    assessment_id uuid PRIMARY KEY REFERENCES assessment(id),
    schema_version text NOT NULL,
    classification_conditions jsonb CHECK (jsonb_typeof(classification_conditions) = 'object'),
    classification_result text CHECK (classification_result IN ('meets','not_meets','insufficient','not_applicable')),
    ms_contributions jsonb CHECK (jsonb_typeof(ms_contributions) = 'object'),
    ms_total finite_decimal,
    ms_threshold_result boolean,
    CHECK ((ms_total IS NULL) = (ms_threshold_result IS NULL)),
    CHECK (ms_total IS NULL OR ms_contributions IS NOT NULL)
);
CREATE TABLE contact_version (
    id uuid PRIMARY KEY,
    contact_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    contact_name text NOT NULL,
    purpose text NOT NULL,
    channel text NOT NULL,
    channel_value text NOT NULL,
    owner_id uuid NOT NULL REFERENCES principal(id),
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    approval_config_id uuid NOT NULL REFERENCES config_version(id),
    verified_at timestamptz,
    updated_at timestamptz NOT NULL,
    valid_until timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('draft','approved','active','disabled')),
    CONSTRAINT contact_verified_state_requires_time CHECK (status NOT IN ('approved','active') OR verified_at IS NOT NULL),
    UNIQUE (contact_id, version)
);
CREATE TABLE release_bundle (
    id uuid PRIMARY KEY,
    environment text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    publication_epoch bigint NOT NULL CHECK (publication_epoch >= 0),
    status text NOT NULL CHECK (status IN ('draft','approved','enabled','disabled')),
    contact_version_id uuid NOT NULL REFERENCES contact_version(id),
    validation_config_id uuid NOT NULL REFERENCES config_version(id),
    activated_at timestamptz,
    disabled_at timestamptz,
    valid_until timestamptz,
    UNIQUE (environment, version),
    CHECK (status <> 'enabled' OR activated_at IS NOT NULL)
);
CREATE TABLE release_component (
    release_id uuid NOT NULL REFERENCES release_bundle(id),
    component_kind text NOT NULL CHECK (component_kind IN ('rule','window','verification','mapping','visibility','content','notification')),
    config_id uuid NOT NULL REFERENCES config_version(id),
    PRIMARY KEY (release_id, component_kind, config_id)
);
CREATE UNIQUE INDEX release_one_enabled ON release_bundle(environment) WHERE status = 'enabled';
CREATE TABLE alert_evaluation (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    task_id uuid NOT NULL UNIQUE,
    environment text NOT NULL,
    policy_id uuid REFERENCES config_version(id),
    window_policy_id uuid REFERENCES config_version(id),
    assessment_at timestamptz NOT NULL,
    known_at timestamptz NOT NULL,
    context_sequence bigint NOT NULL CHECK (context_sequence > 0),
    mode text NOT NULL CHECK (mode IN ('current','replay')),
    status text NOT NULL CHECK (status IN ('configuration_pending','insufficient','completed','failed','superseded')),
    decision text NOT NULL CHECK (decision IN ('met','not_met','unknown')),
    data_epoch bigint NOT NULL CHECK (data_epoch >= 0),
    verification_epoch bigint NOT NULL CHECK (verification_epoch >= 0),
    publication_epoch bigint NOT NULL CHECK (publication_epoch >= 0),
    authorization_version bigint NOT NULL CHECK (authorization_version > 0),
    lease_generation bigint NOT NULL CHECK (lease_generation > 0),
    use_authorization_id uuid NOT NULL,
    input_digest sha256_hex NOT NULL,
    missing_items jsonb NOT NULL CHECK (jsonb_typeof(missing_items) = 'array'),
    computed_at timestamptz NOT NULL,
    validity text NOT NULL CHECK (validity IN ('valid','invalid')),
    UNIQUE (id, patient_id),
    UNIQUE (patient_id, context_sequence),
    FOREIGN KEY (use_authorization_id, patient_id) REFERENCES data_use_authorization(id, patient_id),
    CHECK (status <> 'completed' OR (policy_id IS NOT NULL AND window_policy_id IS NOT NULL)),
    CHECK (status = 'completed' OR decision = 'unknown'),
    CHECK (status <> 'superseded' OR validity = 'invalid')
);
CREATE INDEX evaluation_current ON alert_evaluation(patient_id, assessment_at DESC, context_sequence DESC)
    WHERE mode = 'current';
CREATE TABLE alert_evaluation_input (
    evaluation_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    signal_id text NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    variable_code text NOT NULL,
    input_role text NOT NULL CHECK (input_role IN ('baseline','current','series','context')),
    record_version_id uuid NOT NULL,
    field_id uuid NOT NULL,
    verification_id uuid NOT NULL,
    normalization_id uuid,
    medical_time_id uuid,
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    PRIMARY KEY (evaluation_id, signal_id, position),
    FOREIGN KEY (evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (verification_id, record_version_id, patient_id) REFERENCES verification(id, record_version_id, patient_id),
    FOREIGN KEY (verification_id, field_id) REFERENCES verification_field(verification_id, field_id),
    FOREIGN KEY (normalization_id, field_id, patient_id) REFERENCES normalized_measurement(id, field_id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id)
);
CREATE INDEX evaluation_input_field ON alert_evaluation_input(field_id, evaluation_id);
CREATE TABLE alert_evaluation_assessment (
    evaluation_id uuid NOT NULL,
    assessment_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    role text NOT NULL CHECK (length(trim(role)) > 0),
    PRIMARY KEY (evaluation_id, assessment_id, role),
    FOREIGN KEY (evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id),
    FOREIGN KEY (assessment_id, patient_id) REFERENCES assessment(id, patient_id)
);
CREATE TABLE alert_signal_result (
    evaluation_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    signal_id text NOT NULL,
    schema_version text NOT NULL CHECK (schema_version = 'alert-signal-1'),
    state text NOT NULL CHECK (state IN ('met','not_met','unknown')),
    algorithm_version text NOT NULL,
    input_field_ids uuid[] NOT NULL,
    reason_codes text[] NOT NULL,
    pair_results jsonb NOT NULL CHECK (jsonb_typeof(pair_results) = 'array'),
    PRIMARY KEY (evaluation_id, signal_id),
    FOREIGN KEY (evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id),
    CHECK (state <> 'unknown' OR cardinality(reason_codes) > 0)
);
CREATE TABLE alert_episode (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    rule_family_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('open','closed','invalid')),
    opened_at timestamptz NOT NULL,
    last_evaluation_id uuid NOT NULL,
    last_event_id uuid,
    state_version bigint NOT NULL CHECK (state_version > 0),
    reset_evaluation_id uuid,
    last_assessment_at timestamptz NOT NULL,
    last_context_sequence bigint NOT NULL CHECK (last_context_sequence > 0),
    UNIQUE (id, patient_id),
    FOREIGN KEY (last_evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id),
    FOREIGN KEY (reset_evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id),
    CHECK (status <> 'closed' OR reset_evaluation_id IS NOT NULL)
);
CREATE UNIQUE INDEX episode_one_open ON alert_episode(patient_id, rule_family_key) WHERE status = 'open';
CREATE TABLE alert_decision (
    id uuid PRIMARY KEY,
    evaluation_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    rule_key text NOT NULL,
    state text NOT NULL CHECK (state IN ('met','not_met','unknown')),
    action text NOT NULL CHECK (action IN ('create_event','continue_episode','close_episode','no_event','blocked')),
    reason_codes text[] NOT NULL,
    episode_id uuid,
    sequence bigint NOT NULL CHECK (sequence > 0),
    previous_decision_id uuid,
    UNIQUE (id, patient_id, rule_key),
    UNIQUE (evaluation_id, rule_key),
    UNIQUE (patient_id, rule_key, sequence),
    FOREIGN KEY (evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id),
    FOREIGN KEY (episode_id, patient_id) REFERENCES alert_episode(id, patient_id),
    FOREIGN KEY (previous_decision_id, patient_id, rule_key) REFERENCES alert_decision(id, patient_id, rule_key),
    CHECK (previous_decision_id IS DISTINCT FROM id),
    CHECK ((sequence = 1) = (previous_decision_id IS NULL)),
    CHECK (action NOT IN ('create_event','continue_episode') OR (state = 'met' AND episode_id IS NOT NULL)),
    CHECK (action <> 'close_episode' OR (state = 'not_met' AND episode_id IS NOT NULL)),
    CHECK (action <> 'blocked' OR cardinality(reason_codes) > 0)
);
CREATE TABLE alert_event (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    evaluation_id uuid NOT NULL,
    release_id uuid NOT NULL REFERENCES release_bundle(id),
    trigger_key text NOT NULL,
    trigger_policy_id uuid NOT NULL REFERENCES config_version(id),
    input_snapshot jsonb NOT NULL CHECK (jsonb_typeof(input_snapshot) = 'object'),
    validity text NOT NULL CHECK (validity IN ('valid','invalid')),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    UNIQUE (patient_id, trigger_policy_id, trigger_key),
    FOREIGN KEY (evaluation_id, patient_id) REFERENCES alert_evaluation(id, patient_id)
);
ALTER TABLE alert_episode ADD CONSTRAINT episode_event_fk
    FOREIGN KEY (last_event_id, patient_id) REFERENCES alert_event(id, patient_id);
CREATE TABLE alert_recipient (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    alert_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    channel text NOT NULL,
    delivery_status text NOT NULL CHECK (delivery_status IN ('not_sent','pending','accepted','delivered','failed','uncertain')),
    read_at timestamptz,
    UNIQUE (id, patient_id),
    UNIQUE (alert_id, relation_id, channel),
    FOREIGN KEY (alert_id, patient_id) REFERENCES alert_event(id, patient_id),
    FOREIGN KEY (relation_id, patient_id) REFERENCES relation(id, patient_id)
);
CREATE TABLE notification_attempt (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    send_permit_id uuid NOT NULL UNIQUE,
    provider_idempotency_key text NOT NULL,
    authorized_at timestamptz NOT NULL,
    permit_expires_at timestamptz NOT NULL,
    principal_auth_epoch bigint NOT NULL,
    relation_auth_epoch bigint NOT NULL,
    publication_epoch bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('authorized','started','accepted','failed','uncertain','cancelled')),
    provider_request_id text,
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (recipient_id, attempt_number),
    FOREIGN KEY (recipient_id, patient_id) REFERENCES alert_recipient(id, patient_id),
    CHECK (permit_expires_at > authorized_at)
);
CREATE TABLE notification_receipt (
    id uuid PRIMARY KEY,
    attempt_id uuid NOT NULL REFERENCES notification_attempt(id),
    provider text NOT NULL,
    provider_event_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('accepted','delivered','failed')),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    evidence_sha256 sha256_hex NOT NULL,
    UNIQUE (provider, provider_event_id)
);
CREATE TABLE summary (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    requested_by uuid NOT NULL REFERENCES principal(id),
    from_date date NOT NULL,
    to_date date NOT NULL,
    scope_timezone text NOT NULL,
    include_unknown_time boolean NOT NULL,
    template_id uuid NOT NULL REFERENCES config_version(id),
    visibility_policy_id uuid NOT NULL REFERENCES config_version(id),
    data_epoch bigint NOT NULL,
    verification_epoch bigint NOT NULL,
    validity text NOT NULL CHECK (validity IN ('valid','invalid')),
    task_id uuid NOT NULL,
    generated_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    CHECK (to_date >= from_date)
);
CREATE TABLE summary_item (
    summary_id uuid NOT NULL,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    field_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    PRIMARY KEY (summary_id, ordinal),
    UNIQUE (summary_id, field_id),
    FOREIGN KEY (summary_id, patient_id) REFERENCES summary(id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id)
);

CREATE TABLE task (
    id uuid PRIMARY KEY,
    patient_id uuid REFERENCES patient(id),
    actor_id uuid NOT NULL REFERENCES principal(id),
    request_row_id uuid REFERENCES write_request(id),
    task_type text NOT NULL CHECK (task_type IN ('ocr','assessment','alert_evaluation','summary','notification','disposition','reconcile')),
    schema_version text NOT NULL,
    input_refs jsonb NOT NULL CHECK (jsonb_typeof(input_refs) = 'object'),
    input_sha256 sha256_hex NOT NULL,
    status text NOT NULL CHECK (status IN ('accepted','blocked','queued','running','awaiting_confirmation','completed','failed','uncertain','awaiting_resolution','cancel_requested','cancelled','superseded')),
    status_version bigint NOT NULL CHECK (status_version > 0),
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    lease_owner text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL,
    deadline_at timestamptz NOT NULL,
    UNIQUE (id, patient_id)
);
ALTER TABLE candidate_set ADD CONSTRAINT candidate_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES task(id, patient_id);
ALTER TABLE assessment ADD CONSTRAINT assessment_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES task(id, patient_id);
ALTER TABLE alert_evaluation ADD CONSTRAINT evaluation_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES task(id, patient_id);
ALTER TABLE summary ADD CONSTRAINT summary_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES task(id, patient_id);
CREATE TABLE task_attempt (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES task(id),
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    lease_generation bigint NOT NULL,
    provider_request_id text,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    error_code text,
    status text NOT NULL CHECK (status IN ('running','completed','failed','uncertain','superseded','cancelled')),
    usage_evidence jsonb CHECK (jsonb_typeof(usage_evidence) = 'object'),
    UNIQUE (task_id, attempt_number)
);
CREATE TABLE outbox_event (
    id uuid PRIMARY KEY,
    event_type text NOT NULL,
    schema_version text NOT NULL,
    aggregate_id uuid NOT NULL,
    aggregate_version bigint NOT NULL,
    occurred_at timestamptz NOT NULL,
    causation_id uuid,
    correlation_id uuid NOT NULL,
    task_id uuid REFERENCES task(id),
    resource_refs jsonb NOT NULL CHECK (jsonb_typeof(resource_refs) = 'object'),
    available_at timestamptz NOT NULL,
    lease_generation bigint NOT NULL DEFAULT 0,
    lease_owner text,
    lease_until timestamptz,
    published_at timestamptz
);
CREATE INDEX outbox_dispatch ON outbox_event(available_at, id) WHERE published_at IS NULL;
CREATE INDEX task_pending ON task(status, deadline_at, id) WHERE status NOT IN ('completed','failed','cancelled','superseded');
CREATE TABLE consumer_receipt (
    consumer text NOT NULL,
    event_id uuid NOT NULL REFERENCES outbox_event(id),
    processed_at timestamptz NOT NULL,
    effect_resource_id uuid NOT NULL,
    PRIMARY KEY (consumer, event_id)
);
CREATE TABLE dependency_node (
    id uuid PRIMARY KEY,
    patient_id uuid REFERENCES patient(id),
    resource_type text NOT NULL,
    resource_id uuid NOT NULL,
    resource_version text NOT NULL,
    validity text NOT NULL CHECK (validity IN ('valid','invalid')),
    UNIQUE (resource_type, resource_id, resource_version)
);
CREATE TABLE dependency_edge (
    dependent_id uuid NOT NULL REFERENCES dependency_node(id),
    prerequisite_id uuid NOT NULL REFERENCES dependency_node(id),
    edge_type text NOT NULL CHECK (edge_type IN ('source','verification','mapping','policy','input','publication','contact')),
    PRIMARY KEY (dependent_id, prerequisite_id, edge_type),
    CHECK (dependent_id <> prerequisite_id)
);
CREATE INDEX dependency_reverse ON dependency_edge(prerequisite_id, dependent_id);
CREATE TABLE audit_event (
    id uuid PRIMARY KEY,
    actor_id uuid REFERENCES principal(id),
    patient_id uuid REFERENCES patient(id),
    action text NOT NULL,
    object_type text NOT NULL,
    object_id uuid,
    object_version text,
    request_id uuid,
    occurred_at timestamptz NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('allowed','denied','committed','failed')),
    reason_code text,
    retention_policy_id uuid NOT NULL REFERENCES retention_policy(id)
);
CREATE INDEX audit_object ON audit_event(object_type, object_id, occurred_at DESC);
CREATE TABLE product_event (
    id uuid PRIMARY KEY,
    event_code text NOT NULL CHECK (event_code IN ('EV-01','EV-02','EV-03','EV-04','EV-05','EV-06','EV-07')),
    request_id uuid,
    resource_id uuid,
    resource_version text,
    outcome text NOT NULL,
    duration_ms bigint CHECK (duration_ms >= 0),
    occurred_at timestamptz NOT NULL
);
CREATE TABLE disposition_request (
    id uuid PRIMARY KEY,
    patient_id uuid REFERENCES patient(id),
    requested_by uuid NOT NULL REFERENCES principal(id),
    policy_id uuid NOT NULL REFERENCES retention_policy(id),
    evidence_ref text NOT NULL,
    scope jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object'),
    status text NOT NULL CHECK (status IN ('pending','approved','running','blocked','completed','rejected')),
    approved_by uuid REFERENCES principal(id),
    approved_at timestamptz,
    created_at timestamptz NOT NULL,
    completed_at timestamptz
);
CREATE TABLE disposition_item (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES disposition_request(id),
    target_type text NOT NULL,
    target_id text NOT NULL,
    target_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','blocked','retained','deleted','failed','awaiting_provider')),
    reason text,
    retention_evidence_ref text,
    recheck_at timestamptz,
    executed_by uuid REFERENCES principal(id),
    executed_at timestamptz,
    receipt_ref text,
    receipt_sha256 sha256_hex,
    UNIQUE (request_id, target_type, target_id, target_version),
    CHECK (status <> 'retained' OR (retention_evidence_ref IS NOT NULL AND recheck_at IS NOT NULL)),
    CHECK (status <> 'deleted' OR (executed_at IS NOT NULL AND receipt_ref IS NOT NULL))
);
CREATE TABLE ingestion_run (
    id uuid PRIMARY KEY,
    source_revision_id uuid NOT NULL REFERENCES source_revision(id),
    dataset_version text NOT NULL,
    checksum sha256_hex NOT NULL,
    schema_version text NOT NULL,
    operator_id uuid NOT NULL REFERENCES principal(id),
    environment text NOT NULL,
    status text NOT NULL CHECK (status IN ('validating','blocked','importing','completed','failed')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    validation_report_ref text,
    UNIQUE (source_revision_id, dataset_version)
);
-- 所有患儿数据访问仍须经过领域权限服务；本文件不授予应用账号权限。
REVOKE ALL ON SCHEMA sjia FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA sjia FROM PUBLIC;
