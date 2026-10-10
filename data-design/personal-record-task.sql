CREATE TABLE sjia.personal_record_task (
  id uuid PRIMARY KEY,
  patient_id uuid NOT NULL REFERENCES sjia.patient(id) ON DELETE RESTRICT,
  owner_id uuid NOT NULL REFERENCES sjia.principal(id) ON DELETE RESTRICT,
  version bigint NOT NULL CHECK (version > 0),
  title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  description text CHECK (description IS NULL OR length(btrim(description)) BETWEEN 1 AND 4000),
  status text NOT NULL CHECK (status IN ('pending', 'completed', 'cancelled')),
  retention_policy_id uuid NOT NULL REFERENCES sjia.retention_policy(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
  finished_at timestamptz,
  UNIQUE (id, patient_id),
  CHECK ((status = 'pending' AND finished_at IS NULL)
    OR (status IN ('completed', 'cancelled') AND finished_at IS NOT NULL AND finished_at >= created_at))
);
CREATE INDEX personal_record_task_owner ON sjia.personal_record_task (owner_id, patient_id, updated_at DESC, id);
