SET search_path TO sjia, public;

-- Q02：家庭原始字段查询；调用前仍需执行用途和环境准入。
PREPARE family_current_fields(uuid, uuid) AS
SELECT r.id AS record_id, rv.id AS record_version_id, rv.version,
       f.id AS field_id, f.field_code, f.concept_code, f.original_name,
       f.raw_text, f.raw_numeric::text, f.qualifier, f.raw_unit,
       f.method, f.reference_interval, f.value_state,
       mt.precision, mt.raw_text AS medical_time_text, mt.local_date, mt.instant, mt.resolution_status,
       mt.local_year, mt.local_month, mt.end_year, mt.end_month, mt.relative_text,
       rv.origin_type, rv.quality_status, rv.recorded_at
FROM record r
JOIN record_version rv ON (rv.record_id, rv.patient_id, rv.version) = (r.id, r.patient_id, r.current_version)
JOIN record_field f ON (f.record_version_id, f.patient_id) = (rv.id, rv.patient_id)
LEFT JOIN medical_time mt ON (mt.id, mt.patient_id) = (f.medical_time_id, f.patient_id)
WHERE r.patient_id = $2 AND r.status = 'active'
  AND r.kind IN ('observation','report','therapy')
  AND EXISTS (
    SELECT 1 FROM principal p
    JOIN relation rel ON rel.guardian_id = p.id
    JOIN access_grant g ON (g.relation_id, g.patient_id) = (rel.id, rel.patient_id)
    WHERE p.id = $1 AND p.account_status = 'active'
      AND rel.patient_id = $2 AND rel.status = 'active'
      AND rel.valid_from <= CURRENT_TIMESTAMP
      AND (rel.valid_until IS NULL OR rel.valid_until > CURRENT_TIMESTAMP)
      AND g.principal_id = p.id AND g.action = 'read_raw'
      AND g.revoked_at IS NULL AND g.valid_from <= CURRENT_TIMESTAMP
      AND (g.valid_until IS NULL OR g.valid_until > CURRENT_TIMESTAMP)
      AND f.field_code = ANY(g.field_codes)
      AND (g.record_scope = 'all' OR (g.record_scope = 'own' AND rv.author_id = p.id)
           OR (g.record_scope = 'assigned' AND g.assigned_record_id = r.id))
  )
ORDER BY mt.precision, mt.instant DESC NULLS LAST, mt.local_date DESC NULLS LAST, f.id;

-- Q04：返回候选核对证据；执行计算前继续检查岗位、来源、策略及适用时间。
PREPARE current_field_verifications(uuid, uuid, text) AS
SELECT f.id AS field_id, rv.id AS record_version_id, v.id AS verification_id,
       v.policy_id, v.evidence_source_id, v.verified_at
FROM record_field f
JOIN record_version rv ON (rv.id, rv.patient_id) = (f.record_version_id, f.patient_id)
JOIN record r ON (r.id, r.patient_id, r.current_version) = (rv.record_id, rv.patient_id, rv.version)
JOIN verification_field vf ON (vf.field_id, vf.record_version_id, vf.patient_id) = (f.id, rv.id, f.patient_id)
JOIN verification v ON (v.id, v.record_version_id, v.patient_id) = (vf.verification_id, rv.id, f.patient_id)
JOIN field_verification_state fs ON (fs.field_id, fs.record_version_id, fs.patient_id) = (f.id, rv.id, f.patient_id)
WHERE f.id = $1 AND f.patient_id = $2 AND r.status = 'active'
  AND fs.purpose = $3 AND fs.status = 'verified' AND fs.current_verification_id = v.id
  AND v.purpose = fs.purpose AND v.revoked_at IS NULL AND vf.result = 'consistent';

-- Q06：查询本人请求，返回资源标识后再次执行该资源的当前授权。
PREPARE own_request(uuid, uuid, text, text) AS
SELECT wr.status, wr.resource_type, wr.resource_id, wr.completed_at, wr.error_code
FROM write_request wr JOIN principal p ON p.id = wr.actor_id
WHERE wr.actor_id = $1 AND wr.request_id = $2 AND wr.action = $3 AND wr.target_key = $4
  AND p.account_status = 'active'
  AND (wr.patient_id IS NULL OR EXISTS (
    SELECT 1 FROM relation rel JOIN access_grant g
      ON (g.relation_id, g.patient_id) = (rel.id, rel.patient_id)
    WHERE rel.guardian_id = p.id AND rel.patient_id = wr.patient_id AND rel.status = 'active'
      AND rel.valid_from <= CURRENT_TIMESTAMP
      AND (rel.valid_until IS NULL OR rel.valid_until > CURRENT_TIMESTAMP)
      AND g.principal_id = p.id AND g.action = 'view_request'
      AND g.revoked_at IS NULL AND g.valid_from <= CURRENT_TIMESTAMP
      AND (g.valid_until IS NULL OR g.valid_until > CURRENT_TIMESTAMP)
  ));

-- Q10：字典版本生效资格由领域服务复核，歧义候选全部返回。
PREPARE dictionary_exact_matches(uuid, text) AS
SELECT DISTINCT e.id, e.concept_code, e.label, e.dictionary_version_id
FROM dictionary_entry e
JOIN config_version c ON c.id = e.dictionary_version_id
LEFT JOIN dictionary_alias a ON (a.entry_id, a.dictionary_version_id) = (e.id, e.dictionary_version_id)
WHERE c.id = $1 AND c.kind = 'dictionary' AND c.status = 'enabled'
  AND (e.concept_code = $2 OR e.label = $2 OR a.normalized_alias = $2)
ORDER BY e.concept_code, e.id;

-- Dispatcher 在事务中领取投递租约；网络发送在事务外完成。
PREPARE claim_outbox(integer, text, timestamptz) AS
WITH selected AS (
  SELECT id FROM outbox_event
  WHERE published_at IS NULL AND available_at <= CURRENT_TIMESTAMP
    AND (lease_until IS NULL OR lease_until < CURRENT_TIMESTAMP)
  ORDER BY available_at, id
  FOR UPDATE SKIP LOCKED LIMIT $1
)
UPDATE outbox_event e
SET lease_owner = $2, lease_until = $3, lease_generation = e.lease_generation + 1
FROM selected s WHERE e.id = s.id
RETURNING e.id, e.task_id, e.schema_version, e.lease_generation;

-- Q24：最新当前评估候选；领域服务继续复核配置、用途及当前有效期。
PREPARE current_alert_evaluation(uuid) AS
SELECT e.*, p.data_epoch AS current_data_epoch, p.verification_epoch AS current_verification_epoch
FROM alert_evaluation e JOIN patient p ON p.id = e.patient_id
WHERE e.patient_id = $1 AND e.mode = 'current'
ORDER BY e.assessment_at DESC, e.context_sequence DESC
LIMIT 1;

-- Q25：准确评估输入；复核来源与核对资格后采用。
PREPARE alert_evaluation_sources(uuid, uuid) AS
SELECT i.*, rv.record_id, rv.version, v.revoked_at, vf.result,
       fs.status AS current_verification_status, fs.current_verification_id,
       f.medical_time_id AS field_medical_time_id, rv.source_revision_id AS record_source_revision_id
FROM alert_evaluation_input i
JOIN record_field f ON (f.id, f.record_version_id, f.patient_id) = (i.field_id, i.record_version_id, i.patient_id)
JOIN record_version rv ON (rv.id, rv.patient_id) = (i.record_version_id, i.patient_id)
JOIN verification v ON (v.id, v.record_version_id, v.patient_id) = (i.verification_id, i.record_version_id, i.patient_id)
JOIN verification_field vf ON (vf.verification_id, vf.field_id) = (i.verification_id, i.field_id)
LEFT JOIN field_verification_state fs ON fs.field_id = i.field_id AND fs.purpose = v.purpose
WHERE i.evaluation_id = $1 AND i.patient_id = $2
ORDER BY i.signal_id, i.position;

-- Q26：从准确依赖节点传播失效；调用方持有相关患儿和发布锁。
PREPARE invalidate_alert_dependencies(uuid) AS
WITH RECURSIVE affected(id) AS (
    SELECT id FROM dependency_node WHERE id = $1
    UNION
    SELECT e.dependent_id FROM dependency_edge e JOIN affected a ON e.prerequisite_id = a.id
), invalid_nodes AS (
    UPDATE dependency_node n SET validity = 'invalid'
    WHERE n.id IN (SELECT id FROM affected)
    RETURNING resource_type, resource_id
), invalid_evaluations AS (
    UPDATE alert_evaluation SET validity = 'invalid'
    WHERE id IN (SELECT resource_id FROM invalid_nodes WHERE resource_type = 'alert_evaluation')
    RETURNING id
), invalid_events AS (
    UPDATE alert_event SET validity = 'invalid'
    WHERE evaluation_id IN (SELECT id FROM invalid_evaluations)
       OR id IN (SELECT resource_id FROM invalid_nodes WHERE resource_type = 'alert_event')
    RETURNING id
), invalid_assessments AS (
    UPDATE assessment SET validity = 'invalid'
    WHERE id IN (SELECT resource_id FROM invalid_nodes WHERE resource_type = 'assessment')
    RETURNING id
), invalid_summaries AS (
    UPDATE summary SET validity = 'invalid'
    WHERE id IN (SELECT resource_id FROM invalid_nodes WHERE resource_type = 'summary')
    RETURNING id
), invalid_episodes AS (
    UPDATE alert_episode SET status = 'invalid', state_version = state_version + 1
    WHERE status <> 'invalid' AND (last_evaluation_id IN (SELECT id FROM invalid_evaluations)
       OR last_event_id IN (SELECT id FROM invalid_events))
    RETURNING id
)
SELECT (SELECT count(*) FROM invalid_nodes) AS nodes,
       (SELECT count(*) FROM invalid_evaluations) AS evaluations,
       (SELECT count(*) FROM invalid_events) AS events,
       (SELECT count(*) FROM invalid_assessments) AS assessments,
       (SELECT count(*) FROM invalid_summaries) AS summaries,
       (SELECT count(*) FROM invalid_episodes) AS episodes;

-- Q27：准确记录版本的全部原件清单；逐文件授权之后形成家庭投影。
PREPARE record_original_files(uuid, uuid) AS
SELECT a.ordinal, f.id, f.file_group_id, f.object_version, f.checksum, f.status
FROM record_attachment a JOIN file_object f ON (f.id, f.patient_id) = (a.file_id, a.patient_id)
WHERE a.record_version_id = $1 AND a.patient_id = $2
ORDER BY a.ordinal;

DEALLOCATE ALL;
