-- 后端运行结构；依次执行数据基础结构、病例结构和本文件。
SET search_path TO sjia, public;

CREATE TABLE environment_gate (
    environment varchar(64) PRIMARY KEY,
    version bigint NOT NULL CHECK (version > 0),
    publication_epoch bigint NOT NULL CHECK (publication_epoch >= 0),
    enabled boolean NOT NULL,
    active_release_id uuid REFERENCES release_bundle(id),
    runtime_config_id uuid REFERENCES config_version(id),
    updated_at timestamptz NOT NULL,
    CHECK (NOT enabled OR runtime_config_id IS NOT NULL)
);

CREATE TABLE service_role_grant (
    id uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES principal(id),
    environment varchar(64) NOT NULL REFERENCES environment_gate(environment),
    action varchar(64) NOT NULL,
    granted_by uuid NOT NULL REFERENCES principal(id),
    evidence_ref text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_until timestamptz,
    revoked_at timestamptz,
    revoked_by uuid REFERENCES principal(id),
    revocation_reason text,
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (revoked_at IS NULL OR (revoked_by IS NOT NULL AND revocation_reason IS NOT NULL))
);
CREATE INDEX service_role_lookup ON service_role_grant(principal_id, environment, action);

CREATE TABLE write_intent (
    id uuid PRIMARY KEY,
    actor_id uuid NOT NULL REFERENCES principal(id),
    patient_id uuid REFERENCES patient(id),
    operation_id varchar(80) NOT NULL,
    target_key varchar(256) NOT NULL,
    request_id uuid NOT NULL,
    payload_sha256 sha256_hex NOT NULL,
    normalization_version varchar(32) NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    status text NOT NULL CHECK (status IN ('prepared','committed','failed')),
    prepared_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    completed_at timestamptz,
    write_request_id uuid REFERENCES write_request(id),
    error_code varchar(64),
    retention_policy_id uuid NOT NULL REFERENCES retention_policy(id),
    UNIQUE (actor_id, operation_id, target_key, request_id),
    CHECK (expires_at > prepared_at),
    CHECK ((status = 'prepared' AND completed_at IS NULL AND write_request_id IS NULL AND error_code IS NULL)
        OR (status = 'committed' AND completed_at IS NOT NULL AND write_request_id IS NOT NULL AND error_code IS NULL)
        OR (status = 'failed' AND completed_at IS NOT NULL AND write_request_id IS NULL AND error_code IS NOT NULL))
);
CREATE INDEX write_intent_expiry ON write_intent(expires_at, id) WHERE status = 'prepared';
CREATE INDEX write_intent_owner ON write_intent(actor_id, patient_id, prepared_at DESC, id);

CREATE TABLE upload_part (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    file_group_id uuid NOT NULL,
    client_upload_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    declared_checksum sha256_hex NOT NULL,
    declared_size_bytes bigint NOT NULL CHECK (declared_size_bytes > 0),
    declared_media_type varchar(128) NOT NULL,
    status text NOT NULL CHECK (status IN ('declared','receiving','stored','failed','uncertain')),
    object_key text NOT NULL UNIQUE,
    object_version text,
    file_id uuid,
    intent_id uuid REFERENCES write_intent(id),
    created_at timestamptz NOT NULL,
    UNIQUE (file_group_id, ordinal),
    UNIQUE (file_group_id, client_upload_id),
    FOREIGN KEY (file_group_id, patient_id) REFERENCES file_group(id, patient_id),
    FOREIGN KEY (file_id, patient_id) REFERENCES file_object(id, patient_id),
    CHECK (status <> 'stored' OR (file_id IS NOT NULL AND object_version IS NOT NULL))
);

ALTER TABLE task DROP CONSTRAINT task_task_type_check;
ALTER TABLE task ADD CONSTRAINT task_task_type_check CHECK
    (task_type IN ('file_validation','ocr','assessment','alert_evaluation','summary','notification','disposition','reconcile'));

CREATE TABLE config_validation (
    id uuid PRIMARY KEY,
    config_id uuid NOT NULL REFERENCES config_version(id),
    config_checksum sha256_hex NOT NULL,
    environment varchar(64) NOT NULL REFERENCES environment_gate(environment),
    artifact_sha256 sha256_hex NOT NULL,
    dataset_revision_id uuid NOT NULL REFERENCES source_revision(id),
    evidence_ref text NOT NULL,
    evidence_sha256 sha256_hex NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('pass','fail')),
    executed_by uuid NOT NULL REFERENCES principal(id),
    executed_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revocation_reason text,
    CHECK (revoked_at IS NULL OR revocation_reason IS NOT NULL)
);
CREATE INDEX config_validation_lookup ON config_validation(config_id, environment, executed_at DESC);

CREATE TABLE correction_request (
    id uuid PRIMARY KEY,
    patient_id uuid NOT NULL,
    record_version_id uuid NOT NULL,
    requested_by uuid NOT NULL REFERENCES principal(id),
    field_ids uuid[] NOT NULL CHECK (cardinality(field_ids) > 0),
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    proposed_text text,
    status text NOT NULL CHECK (status IN ('pending','in_review','resolved','rejected','withdrawn','superseded')),
    version bigint NOT NULL CHECK (version > 0),
    created_at timestamptz NOT NULL,
    resolved_by uuid REFERENCES principal(id),
    resolved_at timestamptz,
    resolution_record_version_id uuid,
    resolution_reason text,
    FOREIGN KEY (record_version_id, patient_id) REFERENCES record_version(id, patient_id),
    FOREIGN KEY (resolution_record_version_id, patient_id) REFERENCES record_version(id, patient_id),
    CHECK (status NOT IN ('resolved','rejected') OR
        (resolved_by IS NOT NULL AND resolved_at IS NOT NULL AND resolution_reason IS NOT NULL)),
    CHECK (status <> 'resolved' OR resolution_record_version_id IS NOT NULL)
);
CREATE INDEX correction_request_scope ON correction_request(patient_id, status, created_at, id);

CREATE INDEX audit_actor_action_time ON audit_event(actor_id, action, occurred_at DESC);
CREATE INDEX audit_provider_state ON audit_event(object_id, action, occurred_at DESC)
    WHERE object_type = 'provider_config';

-- 领域服务核验配置种类、环境一致、字段所属版本及合法状态转换。
REVOKE ALL ON ALL TABLES IN SCHEMA sjia FROM PUBLIC;
