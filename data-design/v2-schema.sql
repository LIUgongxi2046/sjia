SET search_path TO sjia, public;

ALTER TABLE verification ADD CONSTRAINT v2_verification_patient_unique UNIQUE (id, patient_id);
ALTER TABLE task_attempt ADD CONSTRAINT v2_task_attempt_task_unique UNIQUE (id, task_id);

CREATE TABLE archive (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL UNIQUE REFERENCES patient(id),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id)
);
CREATE TABLE archive_batch (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    archive_id uuid NOT NULL,
    submitted_by uuid NOT NULL REFERENCES principal(id),
    logical_request_id uuid NOT NULL UNIQUE,
    version bigint NOT NULL CHECK (version > 0),
    received_at timestamptz NOT NULL,
    quota_policy_id uuid NOT NULL REFERENCES config_version(id),
    budget_status text NOT NULL CHECK (budget_status IN ('reserved','available','exhausted','unknown','released')),
    status text NOT NULL CHECK (status IN ('receiving','processing','awaiting_confirmation','partial','archived','cancelled','failed')),
    UNIQUE (id, patient_id),
    FOREIGN KEY (archive_id, patient_id) REFERENCES archive(id, patient_id)
);
CREATE INDEX archive_batch_patient_order ON archive_batch(patient_id, received_at DESC, id);
CREATE TABLE archive_document (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    current_version bigint NOT NULL CHECK (current_version > 0),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id)
);
CREATE TABLE archive_document_version (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    document_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    batch_id uuid NOT NULL,
    document_type text NOT NULL CHECK (document_type IN ('laboratory','inpatient','discharge','outpatient','therapy','imaging_report','imaging_asset','unknown')),
    organization text,
    proposed_metadata jsonb CHECK (proposed_metadata IS NULL OR jsonb_typeof(proposed_metadata) = 'object'),
    confirmed_text text,
    grouping_status text NOT NULL CHECK (grouping_status IN ('pending','confirmed','needs_correction')),
    documented_time_id uuid,
    available_at timestamptz NOT NULL,
    confirmed_by uuid REFERENCES principal(id),
    confirmed_at timestamptz,
    supersedes_version bigint,
    reason text,
    UNIQUE (id, patient_id),
    UNIQUE (document_id, patient_id, version),
    FOREIGN KEY (document_id, patient_id) REFERENCES archive_document(id, patient_id),
    FOREIGN KEY (batch_id, patient_id) REFERENCES archive_batch(id, patient_id),
    FOREIGN KEY (documented_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (document_id, patient_id, supersedes_version) REFERENCES archive_document_version(document_id, patient_id, version),
    CHECK (grouping_status <> 'confirmed' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
    CHECK (supersedes_version IS NULL OR (supersedes_version < version AND reason IS NOT NULL))
);
ALTER TABLE archive_document ADD CONSTRAINT archive_document_current_fk
    FOREIGN KEY (id, patient_id, current_version) REFERENCES archive_document_version(document_id, patient_id, version) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX archive_document_directory ON archive_document_version(patient_id, organization, document_type, id);
CREATE TABLE archive_page (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    file_page_id uuid NOT NULL,
    file_id uuid NOT NULL,
    file_object_version text NOT NULL,
    checksum sha256_hex NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('declared','receiving','uploaded','queued','processing','awaiting_confirmation','confirmed','archived','failed','uncertain','cancel_requested','cancelled','superseded')),
    status_version bigint NOT NULL CHECK (status_version > 0),
    current_task_id uuid,
    available_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    UNIQUE (batch_id, file_page_id),
    FOREIGN KEY (batch_id, patient_id) REFERENCES archive_batch(id, patient_id),
    FOREIGN KEY (file_page_id, patient_id) REFERENCES file_page(id, patient_id),
    FOREIGN KEY (file_id, patient_id) REFERENCES file_object(id, patient_id),
    FOREIGN KEY (current_task_id, patient_id) REFERENCES task(id, patient_id)
);
CREATE INDEX archive_page_batch_status ON archive_page(batch_id, status, id);
CREATE TABLE archive_document_page (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    document_version_id uuid NOT NULL,
    page_id uuid NOT NULL,
    page_order integer NOT NULL CHECK (page_order > 0),
    UNIQUE (document_version_id, page_order),
    UNIQUE (document_version_id, page_id),
    FOREIGN KEY (document_version_id, patient_id) REFERENCES archive_document_version(id, patient_id),
    FOREIGN KEY (page_id, patient_id) REFERENCES archive_page(id, patient_id)
);
CREATE TABLE archive_page_attempt (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    page_id uuid NOT NULL,
    page_version bigint NOT NULL CHECK (page_version > 0),
    task_id uuid NOT NULL,
    task_attempt_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    input_sha256 sha256_hex NOT NULL,
    model_version text NOT NULL,
    candidate_set_id uuid,
    provider_request_id text,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    duration_ms bigint CHECK (duration_ms IS NULL OR duration_ms >= 0),
    cost numeric CHECK (cost IS NULL OR (cost >= 0 AND cost <> 'NaN'::numeric AND cost <> 'Infinity'::numeric)),
    currency text,
    usage_evidence jsonb,
    status text NOT NULL CHECK (status IN ('running','completed','failed','uncertain','cancelled','superseded')),
    error_code text,
    UNIQUE (page_id, page_version, attempt_number),
    FOREIGN KEY (page_id, patient_id) REFERENCES archive_page(id, patient_id),
    FOREIGN KEY (task_id, patient_id) REFERENCES task(id, patient_id),
    FOREIGN KEY (task_attempt_id, task_id) REFERENCES task_attempt(id, task_id),
    FOREIGN KEY (candidate_set_id, patient_id) REFERENCES candidate_set(id, patient_id),
    CHECK (status <> 'completed' OR (finished_at IS NOT NULL AND candidate_set_id IS NOT NULL)),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE TABLE archive_fact (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    current_version bigint NOT NULL CHECK (current_version > 0),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id)
);
CREATE TABLE archive_fact_version (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    fact_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    record_version_id uuid NOT NULL,
    field_id uuid NOT NULL,
    document_version_id uuid,
    page_id uuid,
    source_anchor_id uuid,
    context_type text NOT NULL CHECK (context_type IN ('history','current','discharge','planned','actual','unknown')),
    assertion_state text NOT NULL CHECK (assertion_state IN ('present','absent','unknown')),
    episode_id uuid,
    medical_time_id uuid,
    raw_time_text text,
    time_origin text NOT NULL CHECK (time_origin IN ('item','explicit_common','observation','therapy','unknown','legacy')),
    documented_time_id uuid,
    available_at timestamptz,
    availability_status text NOT NULL CHECK (availability_status IN ('known','unknown')),
    confirmation_id uuid,
    created_at timestamptz NOT NULL,
    supersedes_version bigint,
    reason text,
    UNIQUE (id, patient_id),
    UNIQUE (fact_id, patient_id, version),
    UNIQUE (field_id),
    FOREIGN KEY (fact_id, patient_id) REFERENCES archive_fact(id, patient_id),
    FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES record_field(id, record_version_id, patient_id),
    FOREIGN KEY (document_version_id, patient_id) REFERENCES archive_document_version(id, patient_id),
    FOREIGN KEY (page_id, patient_id) REFERENCES archive_page(id, patient_id),
    FOREIGN KEY (source_anchor_id, patient_id) REFERENCES source_anchor(id, patient_id),
    FOREIGN KEY (episode_id, patient_id) REFERENCES clinical_episode(id, patient_id),
    FOREIGN KEY (medical_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (documented_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (confirmation_id, patient_id) REFERENCES candidate_confirmation(id, patient_id),
    FOREIGN KEY (fact_id, patient_id, supersedes_version) REFERENCES archive_fact_version(fact_id, patient_id, version),
    CHECK ((availability_status = 'known') = (available_at IS NOT NULL)),
    CHECK (supersedes_version IS NULL OR (supersedes_version < version AND reason IS NOT NULL)),
    CHECK (document_version_id IS NULL OR (page_id IS NOT NULL AND source_anchor_id IS NOT NULL))
);
ALTER TABLE archive_fact ADD CONSTRAINT archive_fact_current_fk
    FOREIGN KEY (id, patient_id, current_version) REFERENCES archive_fact_version(fact_id, patient_id, version) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX archive_fact_time ON archive_fact_version(patient_id, medical_time_id, id);
CREATE TABLE archive_document_episode (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    document_version_id uuid NOT NULL,
    episode_id uuid NOT NULL,
    evidence_anchor_id uuid NOT NULL,
    confirmed_by uuid NOT NULL REFERENCES principal(id),
    confirmed_at timestamptz NOT NULL,
    UNIQUE (document_version_id, episode_id),
    FOREIGN KEY (document_version_id, patient_id) REFERENCES archive_document_version(id, patient_id),
    FOREIGN KEY (episode_id, patient_id) REFERENCES clinical_episode(id, patient_id),
    FOREIGN KEY (evidence_anchor_id, patient_id) REFERENCES source_anchor(id, patient_id)
);
CREATE TABLE archive_relation_version (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    relation_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    left_fact_version_id uuid,
    right_fact_version_id uuid,
    left_file_id uuid,
    right_file_id uuid,
    relation_type text NOT NULL CHECK (relation_type IN ('same_bytes','document_copy','same_sampling','independent_sampling','formal_correction')),
    status text NOT NULL CHECK (status IN ('proposed','confirmed','disputed','released')),
    evidence_ref text NOT NULL,
    reason text NOT NULL,
    author_id uuid NOT NULL REFERENCES principal(id),
    created_at timestamptz NOT NULL,
    verification_id uuid,
    supersedes_version bigint,
    UNIQUE (relation_id, patient_id, version),
    UNIQUE (id, patient_id),
    FOREIGN KEY (left_fact_version_id, patient_id) REFERENCES archive_fact_version(id, patient_id),
    FOREIGN KEY (right_fact_version_id, patient_id) REFERENCES archive_fact_version(id, patient_id),
    FOREIGN KEY (left_file_id, patient_id) REFERENCES file_object(id, patient_id),
    FOREIGN KEY (right_file_id, patient_id) REFERENCES file_object(id, patient_id),
    FOREIGN KEY (verification_id, patient_id) REFERENCES verification(id, patient_id),
    FOREIGN KEY (relation_id, patient_id, supersedes_version) REFERENCES archive_relation_version(relation_id, patient_id, version),
    CHECK ((left_fact_version_id IS NOT NULL AND right_fact_version_id IS NOT NULL AND left_file_id IS NULL AND right_file_id IS NULL AND left_fact_version_id <> right_fact_version_id)
        OR (left_file_id IS NOT NULL AND right_file_id IS NOT NULL AND left_fact_version_id IS NULL AND right_fact_version_id IS NULL AND left_file_id <> right_file_id)),
    CHECK (relation_type NOT IN ('same_sampling','independent_sampling','formal_correction') OR left_fact_version_id IS NOT NULL),
    CHECK (supersedes_version IS NULL OR supersedes_version < version)
);
CREATE INDEX archive_relation_fact ON archive_relation_version(patient_id, left_fact_version_id, right_fact_version_id, version DESC);
CREATE TABLE purpose_copy (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    original_file_id uuid NOT NULL,
    derived_file_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    purpose text NOT NULL CHECK (purpose IN ('external_recognition','format_evaluation','clinical_evaluation','competition_display','export')),
    environment text NOT NULL,
    use_authorization_id uuid NOT NULL,
    processing_version text NOT NULL,
    processing_evidence jsonb NOT NULL CHECK (jsonb_typeof(processing_evidence) = 'object'),
    date_strategy text NOT NULL CHECK (date_strategy IN ('unchanged','consistent_shift','unknown')),
    clinical_integrity_status text NOT NULL CHECK (clinical_integrity_status IN ('pending','verified','failed')),
    status text NOT NULL CHECK (status IN ('pending_review','approved','rejected','revoked','disposed')),
    created_by uuid NOT NULL REFERENCES principal(id),
    created_at timestamptz NOT NULL,
    disposition_ref uuid REFERENCES disposition_request(id),
    UNIQUE (id, patient_id),
    UNIQUE (id, patient_id, version),
    UNIQUE (derived_file_id, purpose, environment, version),
    FOREIGN KEY (original_file_id, patient_id) REFERENCES file_object(id, patient_id),
    FOREIGN KEY (derived_file_id, patient_id) REFERENCES file_object(id, patient_id),
    FOREIGN KEY (use_authorization_id, patient_id) REFERENCES data_use_authorization(id, patient_id),
    CHECK (original_file_id <> derived_file_id),
    CHECK (status <> 'approved' OR clinical_integrity_status = 'verified')
);
CREATE TABLE purpose_copy_review (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    copy_id uuid NOT NULL,
    copy_version bigint NOT NULL CHECK (copy_version > 0),
    reviewer_id uuid NOT NULL REFERENCES principal(id),
    reviewed_at timestamptz NOT NULL,
    evidence_ref text NOT NULL,
    checked_scopes jsonb NOT NULL CHECK (jsonb_typeof(checked_scopes) = 'array'),
    identity_result text NOT NULL CHECK (identity_result IN ('passed','failed')),
    clinical_result text NOT NULL CHECK (clinical_result IN ('passed','failed')),
    original_checksum sha256_hex NOT NULL,
    derived_checksum sha256_hex NOT NULL,
    revoked_at timestamptz,
    FOREIGN KEY (copy_id, patient_id, copy_version) REFERENCES purpose_copy(id, patient_id, version)
);
CREATE TABLE observation_period_version (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    period_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    owner_id uuid NOT NULL REFERENCES principal(id),
    start_time_id uuid NOT NULL,
    end_time_id uuid NOT NULL,
    label text,
    label_source text NOT NULL CHECK (label_source IN ('self_report','authorized_clinical')),
    episode_id uuid,
    created_at timestamptz NOT NULL,
    supersedes_version bigint,
    UNIQUE (id, patient_id),
    UNIQUE (period_id, patient_id, version),
    FOREIGN KEY (start_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (end_time_id, patient_id) REFERENCES medical_time(id, patient_id),
    FOREIGN KEY (episode_id, patient_id) REFERENCES clinical_episode(id, patient_id),
    FOREIGN KEY (period_id, patient_id, supersedes_version) REFERENCES observation_period_version(period_id, patient_id, version),
    CHECK (supersedes_version IS NULL OR supersedes_version < version)
);
CREATE TABLE delta_summary (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    owner_id uuid NOT NULL REFERENCES principal(id),
    batch_id uuid NOT NULL,
    baseline_refs jsonb CHECK (baseline_refs IS NULL OR jsonb_typeof(baseline_refs) = 'array'),
    current_refs jsonb NOT NULL CHECK (jsonb_typeof(current_refs) = 'array'),
    data_epoch bigint NOT NULL,
    authorization_version bigint NOT NULL,
    visibility_policy_id uuid NOT NULL REFERENCES config_version(id),
    validity text NOT NULL CHECK (validity IN ('valid','invalid')),
    generated_at timestamptz NOT NULL,
    task_id uuid NOT NULL,
    UNIQUE (id, patient_id),
    FOREIGN KEY (batch_id, patient_id) REFERENCES archive_batch(id, patient_id),
    FOREIGN KEY (task_id, patient_id) REFERENCES task(id, patient_id)
);
CREATE TABLE delta_summary_item (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    summary_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    item_kind text NOT NULL CHECK (item_kind IN ('document','fact','duplicate','conflict','unknown_time','unverified','change')),
    item_source_refs jsonb NOT NULL CHECK (jsonb_typeof(item_source_refs) = 'array' AND jsonb_array_length(item_source_refs) > 0),
    limitations jsonb NOT NULL CHECK (jsonb_typeof(limitations) = 'array'),
    content jsonb NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    UNIQUE (summary_id, ordinal),
    FOREIGN KEY (summary_id, patient_id) REFERENCES delta_summary(id, patient_id)
);
CREATE TABLE evaluation_dataset_version (
    id uuid PRIMARY KEY,
    dataset_id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    purpose text NOT NULL,
    environment text NOT NULL,
    created_by uuid NOT NULL REFERENCES principal(id),
    created_at timestamptz NOT NULL,
    frozen_at timestamptz,
    protocol_ref text NOT NULL,
    coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage) = 'object'),
    checksum sha256_hex NOT NULL,
    status text NOT NULL CHECK (status IN ('draft','frozen','revoked')),
    UNIQUE (dataset_id, version),
    CHECK (status <> 'frozen' OR frozen_at IS NOT NULL)
);
CREATE TABLE evaluation_patient_partition (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    dataset_version_id uuid NOT NULL REFERENCES evaluation_dataset_version(id),
    partition text NOT NULL CHECK (partition IN ('development','validation')),
    use_authorization_id uuid NOT NULL,
    source_refs jsonb NOT NULL CHECK (jsonb_typeof(source_refs) = 'array'),
    UNIQUE (dataset_version_id, patient_id),
    UNIQUE (id, patient_id),
    FOREIGN KEY (use_authorization_id, patient_id) REFERENCES data_use_authorization(id, patient_id)
);
CREATE TABLE evaluation_label (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    partition_id uuid NOT NULL,
    observation_period_id uuid NOT NULL,
    label_state text NOT NULL CHECK (label_state IN ('positive','negative','unknown')),
    reference_answer_ref text,
    reference_recognition_at timestamptz,
    confirmed_by uuid REFERENCES principal(id),
    confirmed_at timestamptz,
    limitations jsonb NOT NULL CHECK (jsonb_typeof(limitations) = 'array'),
    FOREIGN KEY (partition_id, patient_id) REFERENCES evaluation_patient_partition(id, patient_id),
    FOREIGN KEY (observation_period_id, patient_id) REFERENCES observation_period_version(id, patient_id),
    CHECK (label_state = 'unknown' OR (reference_answer_ref IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))
);
CREATE TABLE evaluation_run (
    id uuid PRIMARY KEY,
    dataset_version_id uuid NOT NULL REFERENCES evaluation_dataset_version(id),
    operator_id uuid NOT NULL REFERENCES principal(id),
    implementation_sha256 sha256_hex NOT NULL,
    rules_refs jsonb NOT NULL CHECK (jsonb_typeof(rules_refs) = 'array'),
    input_refs jsonb NOT NULL CHECK (jsonb_typeof(input_refs) = 'array'),
    result_refs jsonb NOT NULL CHECK (jsonb_typeof(result_refs) = 'array'),
    denominators jsonb NOT NULL CHECK (jsonb_typeof(denominators) = 'object'),
    limitations jsonb NOT NULL CHECK (jsonb_typeof(limitations) = 'array'),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('pending','running','completed','failed','insufficient')),
    mode text NOT NULL CHECK (mode = 'replay'),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE TABLE material_reminder (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL REFERENCES patient(id),
    owner_id uuid NOT NULL REFERENCES principal(id),
    version bigint NOT NULL CHECK (version > 0),
    target_type text NOT NULL CHECK (target_type IN ('draft','batch','record_task')),
    target_ref uuid NOT NULL,
    target_version bigint NOT NULL CHECK (target_version > 0),
    schedule_at timestamptz,
    timezone_name text NOT NULL,
    quiet_start time,
    quiet_end time,
    status text NOT NULL DEFAULT 'disabled' CHECK (status IN ('disabled','enabled','paused','completed','cancelled')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    CHECK (status <> 'enabled' OR schedule_at IS NOT NULL),
    CHECK ((quiet_start IS NULL) = (quiet_end IS NULL)),
    CHECK (quiet_start IS NULL OR quiet_start <> quiet_end)
);
CREATE INDEX material_reminder_due ON material_reminder(schedule_at, id) WHERE status = 'enabled';
CREATE TABLE material_reminder_notice (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    reminder_id uuid NOT NULL,
    reminder_version bigint NOT NULL CHECK (reminder_version > 0),
    scheduled_at timestamptz NOT NULL,
    channel text NOT NULL,
    deduplication_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('pending','accepted','delivered','failed','uncertain','cancelled')),
    permit_refs jsonb NOT NULL CHECK (jsonb_typeof(permit_refs) = 'object'),
    provider_request_id text,
    receipt_refs jsonb NOT NULL CHECK (jsonb_typeof(receipt_refs) = 'array'),
    delivered_at timestamptz,
    read_at timestamptz,
    created_at timestamptz NOT NULL,
    FOREIGN KEY (reminder_id, patient_id) REFERENCES material_reminder(id, patient_id),
    CHECK (status <> 'delivered' OR delivered_at IS NOT NULL),
    CHECK (read_at IS NULL OR delivered_at IS NOT NULL)
);
