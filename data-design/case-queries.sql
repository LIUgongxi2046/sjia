SET search_path TO sjia, public;

-- 病例完整结构仅向具有 read_case 及该记录全部字段权限的主体提供。
-- 家庭页面继续通过已批准的逐字段投影读取，不能直接复用这些专业查询。
CREATE FUNCTION case_read_scope(actor uuid, requested_patient uuid, requested_purpose text, requested_environment text)
RETURNS SETOF current_case_record_version
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = sjia, pg_temp
AS $scope$
SELECT rv.* FROM current_case_record_version rv
JOIN data_use_authorization a ON (a.id, a.patient_id) = (rv.use_authorization_id, rv.patient_id)
JOIN source_revision sr ON sr.id = a.source_revision_id
JOIN source_registry source ON source.id = sr.source_id
JOIN retention_policy retention ON retention.id = rv.retention_policy_id
WHERE rv.patient_id = requested_patient
  AND a.purpose = requested_purpose AND a.environment = requested_environment
  AND a.revoked_at IS NULL AND (a.valid_until IS NULL OR a.valid_until > CURRENT_TIMESTAMP)
  AND (rv.source_revision_id IS NULL OR rv.source_revision_id = a.source_revision_id)
  AND sr.status = 'approved' AND source.status = 'approved' AND retention.status = 'active'
  AND sr.allowed_use ? requested_purpose
  AND EXISTS (
    SELECT 1 FROM principal p JOIN access_grant g ON g.principal_id = p.id
    WHERE p.id = actor AND p.account_status = 'active' AND g.patient_id = rv.patient_id
      AND g.action = 'read_case' AND g.purpose = requested_purpose
      AND g.revoked_at IS NULL AND g.valid_from <= CURRENT_TIMESTAMP
      AND (g.valid_until IS NULL OR g.valid_until > CURRENT_TIMESTAMP)
      AND (g.record_scope = 'all' OR (g.record_scope = 'own' AND rv.author_id = actor)
           OR (g.record_scope = 'assigned' AND g.assigned_record_id = rv.record_id))
      AND (g.relation_id IS NULL OR EXISTS (
        SELECT 1 FROM relation rel WHERE (rel.id, rel.patient_id, rel.guardian_id) = (g.relation_id, g.patient_id, actor)
          AND rel.status = 'active' AND rel.valid_from <= CURRENT_TIMESTAMP
          AND (rel.valid_until IS NULL OR rel.valid_until > CURRENT_TIMESTAMP)))
      AND NOT EXISTS (SELECT 1 FROM record_field f WHERE f.record_version_id = rv.id
                      AND NOT (f.field_code = ANY(g.field_codes)))
  );
$scope$;
REVOKE ALL ON FUNCTION case_read_scope(uuid, uuid, text, text) FROM PUBLIC;

-- Q13：当前基本资料，当前指针与记录版本必须同时有效。
PREPARE case_patient_profile(uuid, uuid, text, text) AS
SELECT profile.*, age.raw_text AS age_reference_text, age.precision AS age_reference_precision
FROM patient p JOIN patient_profile_version profile ON (profile.id, profile.patient_id) = (p.current_profile_id, p.id)
JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = profile.record_version_id
LEFT JOIN medical_time age ON (age.id, age.patient_id) = (profile.age_reference_time_id, profile.patient_id)
WHERE p.id = $2;

-- Q14：检验与标本分组，保留定性结果、比较符和原始单位。
PREPARE case_laboratory_groups(uuid, uuid, text, text) AS
SELECT g.id AS group_id, g.group_type, g.label, g.specimen_text, g.method_text, g.source_quote,
       f.id AS field_id, f.original_name, f.raw_text, f.raw_numeric::text, f.categorical_value,
       f.qualifier, f.raw_unit, f.reference_interval, f.report_flag,
       t.raw_text AS time_text, t.precision, t.local_date, t.local_year, t.local_month, t.end_year, t.end_month,
       t.relative_text, member.ordinal
FROM record_field_group g JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = g.record_version_id
JOIN record_field_group_member member ON member.group_id = g.id
JOIN record_field f ON (f.id, f.record_version_id, f.patient_id) = (member.field_id, member.record_version_id, member.patient_id)
LEFT JOIN medical_time t ON (t.id, t.patient_id) = (g.medical_time_id, g.patient_id)
WHERE g.patient_id = $2 ORDER BY g.id, member.ordinal;

-- Q15：外部量表，来源总分与分项分别返回，不执行公式重算。
PREPARE case_external_scores(uuid, uuid, text, text) AS
SELECT score.id, score.instrument, score.instrument_version, t.raw_text AS time_text, t.local_date,
       total.raw_numeric::text AS reported_total, total.raw_text AS reported_total_text,
       component.component_code, part.raw_numeric::text AS component_value, part.raw_unit,
       score.context_text, score.note, component.ordinal
FROM external_score_observation score JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = score.record_version_id
JOIN medical_time t ON (t.id, t.patient_id) = (score.medical_time_id, score.patient_id)
JOIN record_field total ON (total.id, total.record_version_id, total.patient_id) = (score.total_field_id, score.record_version_id, score.patient_id)
LEFT JOIN external_score_component component ON component.score_id = score.id
LEFT JOIN record_field part ON (part.id, part.record_version_id, part.patient_id) = (component.field_id, component.record_version_id, component.patient_id)
WHERE score.patient_id = $2 ORDER BY t.local_date NULLS LAST, score.id, component.ordinal;

-- Q16：治疗记录按来源时间展示；相对时间和未知时间不推算日期。
PREPARE case_medication_events(uuid, uuid, text, text) AS
SELECT event.id, event.action, t.raw_text AS time_text, t.precision, t.local_date,
       t.local_year, t.local_month, t.end_year, t.end_month, event.note,
       item.medication_name, item.medication_code, item.dose::text, item.dose_unit,
       item.concentration_text, item.route_text, item.frequency_text, item.interval_value::text, item.interval_unit
FROM medication_event event JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = event.record_version_id
JOIN medication_event_item item ON item.event_id = event.id
LEFT JOIN medical_time t ON (t.id, t.patient_id) = (event.medical_time_id, event.patient_id)
WHERE event.patient_id = $2 ORDER BY event.id, item.ordinal;

-- Q17：随访关联同时检查随访与被引用记录的可读范围。
PREPARE case_followups(uuid, uuid, text, text) AS
WITH scope AS MATERIALIZED (SELECT * FROM case_read_scope($1,$2,$3,$4))
SELECT encounter.id, encounter.kind, encounter.label, t.raw_text AS time_text, t.precision, t.local_date,
       encounter.channel_text, link.relation_kind, statement.label AS outcome_name, statement.status_raw_text,
       groups.id AS group_id, score.id AS score_id, medication.id AS medication_event_id
FROM followup_encounter encounter JOIN scope owner ON owner.id = encounter.record_version_id
JOIN medical_time t ON (t.id, t.patient_id) = (encounter.medical_time_id, encounter.patient_id)
LEFT JOIN followup_reference link ON link.encounter_id = encounter.id
LEFT JOIN clinical_statement statement ON statement.id = link.statement_id
LEFT JOIN record_field_group groups ON groups.id = link.group_id
LEFT JOIN external_score_observation score ON score.id = link.score_id
LEFT JOIN medication_event medication ON medication.id = link.medication_event_id
WHERE encounter.patient_id = $2 AND (link.id IS NULL OR EXISTS (
  SELECT 1 FROM scope target WHERE target.id = COALESCE(statement.record_version_id, groups.record_version_id, score.record_version_id, medication.record_version_id)))
ORDER BY t.local_date NULLS LAST, encounter.id, link.id;

-- Q18：影像报告；没有确认关联的图片通过附件查询单独返回。
PREPARE case_imaging_studies(uuid, uuid, text, text) AS
SELECT study.id, study.modality, study.contrast, study.external_study_ref, site.body_site, site.laterality,
       text.raw_text AS report_text, t.raw_text AS time_text, t.precision
FROM imaging_study study JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = study.record_version_id
JOIN record_field text ON (text.id, text.record_version_id, text.patient_id) = (study.source_field_id, study.record_version_id, study.patient_id)
LEFT JOIN imaging_study_site site ON site.study_id = study.id
LEFT JOIN medical_time t ON (t.id, t.patient_id) = (study.study_time_id, study.patient_id)
WHERE study.patient_id = $2 ORDER BY study.id, site.ordinal;

-- Q19：诊断、病史和查体保留来源陈述，不代表平台计算。
PREPARE case_statements(uuid, uuid, text, text) AS
SELECT statement.*, original.raw_text, t.raw_text AS time_text
FROM clinical_statement statement JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = statement.record_version_id
JOIN record_field original ON (original.id, original.record_version_id, original.patient_id) = (statement.source_field_id, statement.record_version_id, statement.patient_id)
LEFT JOIN medical_time t ON (t.id, t.patient_id) = (statement.medical_time_id, statement.patient_id)
WHERE statement.patient_id = $2 ORDER BY statement.statement_type, statement.id;

-- Q20：资料问题与解决状态，字段及文件详情仍需各自读取鉴权。
PREPARE case_quality_issues(uuid, uuid, text, text) AS
SELECT issue.id, issue.category, issue.description, issue.handling, issue.status, issue.resolution_text
FROM data_quality_issue issue JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = issue.record_version_id
WHERE issue.patient_id = $2 ORDER BY issue.status, issue.id;

-- Q21：就诊过程保留原始区间及机构描述。
PREPARE case_episodes(uuid, uuid, text, text) AS
SELECT episode.*, begin_time.raw_text AS start_text, end_time.raw_text AS end_text
FROM clinical_episode episode JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = episode.record_version_id
LEFT JOIN medical_time begin_time ON (begin_time.id, begin_time.patient_id) = (episode.start_time_id, episode.patient_id)
LEFT JOIN medical_time end_time ON (end_time.id, end_time.patient_id) = (episode.end_time_id, episode.patient_id)
WHERE episode.patient_id = $2 ORDER BY episode.id;

-- Q22：非药物处理单独返回。
PREPARE case_procedures(uuid, uuid, text, text) AS
SELECT procedure.id, procedure.procedure_name, procedure.body_site, t.raw_text AS time_text
FROM procedure_event procedure JOIN case_read_scope($1,$2,$3,$4) scope ON scope.id = procedure.record_version_id
LEFT JOIN medical_time t ON (t.id, t.patient_id) = (procedure.medical_time_id, procedure.patient_id)
WHERE procedure.patient_id = $2 ORDER BY procedure.id;

-- Q23：仅返回文件标识；文件代理须再次检查原件用途与当前访问权限。
PREPARE case_image_attachments(uuid, uuid, text, text) AS
WITH scope AS MATERIALIZED (SELECT * FROM case_read_scope($1,$2,$3,$4))
SELECT link.id, link.file_id, link.link_status,
       CASE WHEN visible_study.id IS NOT NULL THEN link.study_id END AS study_id
FROM imaging_asset_link link JOIN scope owner ON owner.id = link.record_version_id
JOIN file_object file ON (file.id, file.patient_id) = (link.file_id, link.patient_id) AND file.status = 'available'
LEFT JOIN imaging_study study ON study.id = link.study_id
LEFT JOIN scope visible_study ON visible_study.id = study.record_version_id
WHERE link.patient_id = $2 ORDER BY link.id;

DEALLOCATE ALL;
