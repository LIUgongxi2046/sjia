import { useState } from 'react';
import { useParams } from 'react-router';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { CorrectionRecovery } from '../../components/CorrectionRecovery';
import { useAccess } from '../../state/access';
import { useRecordResource } from '../../api/useRecordResource';
import { fieldLabel, type RecordView } from '../../api/records';
import { readCorrection, type CorrectionView } from '../../api/corrections';
import { useCorrectionSubmission } from '../../state/useCorrectionSubmission';

export function CorrectionRequestPage() {
  const access = useAccess(); const { recordId } = useParams();
  return <CorrectionRequestContent key={`${access.principalId}:${access.patientId}:${recordId}`} recordId={recordId ?? ''} />;
}
function CorrectionRequestContent({ recordId }: { recordId: string }) {
  const access = useAccess(); const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useRecordResource<RecordView>(canRead && recordId ? `/api/records/${recordId}` : null, access.patientId, 'RecordView', recordId);
  const record = canRead ? resource.data : null;
  const relation = access.relations.find(row => row.patient_id === access.patientId);
  const available = !!record && !!access.csrfToken && record.allowed_actions?.includes('request_correction') === true
    && relation?.status === 'active' && relation.actions.includes('request_correction') && Date.parse(relation.valid_from) <= Date.now()
    && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const [fields, setFields] = useState<string[]>([]); const [reason, setReason] = useState(''); const [proposal, setProposal] = useState('');
  const [saved, setSaved] = useState<CorrectionView | null>(null); const [composing, setComposing] = useState(false);
  const submission = useCorrectionSubmission(available, `/api/records/${recordId}/correction-requests`, async (receipt, signal) => {
    const value = await readCorrection(receipt.resource_id, signal);
    if (!record || BigInt(value.version) < BigInt(receipt.version)) throw new Error('申请回执与当前记录版本不一致。');
    setSaved(value);
  });
  return <UI2FamilyFrame title="申请专业更正" screenId="UI2-08-C" note="指定字段和原因，由获准人员复核处理。" backTo={`/records/${recordId}`}>
    <UI2ReadState loading={resource.loading || access.loading} error={resource.error ?? (access.error ? new Error(access.error) : null)} onRetry={resource.reload} />
    {record && !available && <section className="status-panel permission"><h2>当前未取得更正申请权限</h2><p>请重新核验当前患儿、关系和字段范围。</p></section>}
    {available && record && <section className="card form-card"><h2>记录版本 {record.version}</h2><p>申请不会直接覆盖专业资料。</p>
      <fieldset disabled={submission.locked} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)}>
        {record.fields.map(field => <label className="switch-row" key={field.field_id}><span>{fieldLabel(field)}</span><input type="checkbox" checked={fields.includes(field.field_id)}
          onChange={event => setFields(current => event.target.checked ? [...current, field.field_id] : current.filter(id => id !== field.field_id))} /></label>)}
        <label className="field"><span>更正申请原因</span><textarea value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="field"><span>建议内容（可选）</span><textarea value={proposal} onChange={event => setProposal(event.target.value)} /></label>
        <button className="button" disabled={submission.locked || composing || !fields.length || !reason.trim()} onClick={() => void submission.start({
          operation_id: 'correction_request_create', target_key: `/api/records/${recordId}/correction-requests`, request_id: crypto.randomUUID(), schema_version: '1.0.0',
          payload: { record_version_id: record.record_version_id, field_ids: fields, reason, proposed_text: proposal.trim() ? proposal : null } })}>提交更正申请</button>
      </fieldset></section>}
    {available && <CorrectionRecovery submission={submission} />}
    {available && saved && <section className="card form-card"><h2>申请状态：{saved.status}</h2><p>申请版本 {saved.version}；原记录版本身份 {saved.request.record_version_id}</p><p>{saved.request.reason}</p>
      {saved.resolution_record_version_id && <p>已关联实际保存的更正版本。</p>}</section>}
  </UI2FamilyFrame>;
}
