import { useEffect, useMemo, useState } from 'react';
import { adoptVoiceInput, cancelVoiceInput, createVoiceInput, lookupVoiceInput, processVoiceInput, readVoiceInput,
  readVoiceInputOptions, type VoiceAdoptionResponse, type VoiceInputOptions, type VoiceInputRead, type VoiceOriginalOption } from '../api/voice';
import { saveConfirmedVoiceText } from '../api/archiveUploads';
import { MedicalTimeInput, emptyTime, timePayload, type TimeInput } from './MedicalTimeInput';
import type { VoiceTranscriptSource } from './VoiceEntry';
import { useAccess } from '../state/access';

export interface ConfirmedVoice {
  temperature?: { value: string; unit: string };
  time?: TimeInput;
  timeField?: 'temp' | 'observation_note';
  description: string;
}

type VoiceInputView = VoiceInputRead;
type SavedVoiceResult = VoiceAdoptionResponse;

interface DecisionRow {
  field_path: 'temperature' | 'temperature_unit' | 'symptoms' | 'observed_time' | 'note';
  decision: 'accepted' | 'modified' | 'unknown' | 'excluded';
  selected_value: string | string[] | Record<string, unknown> | null;
  record_field_codes: string[];
}

const decisionLabels = [
  ['accepted', '采用候选'], ['modified', '修改后采用'], ['unknown', '保留未知'], ['excluded', '不写入'],
] as const;

const purposeStateLabels: Record<VoiceOriginalOption['purpose_state'], string> = {
  not_requested: '尚未发起处理',
  processing: '正在处理',
  pending_review: '等待审核',
  approved: '审核通过',
  rejected: '审核未通过',
  revoked: '授权已撤回',
  disposed: '副本已处置',
  failed: '处理失败',
};

function purposeStateDescription(option: VoiceOriginalOption): string {
  const descriptions: Record<VoiceOriginalOption['purpose_state'], string> = {
    not_requested: '获准支持人员尚未发起此用途的处理。',
    processing: '用途处理任务正在执行。',
    pending_review: '用途副本已准备，等待获准人员审核。',
    approved: option.state === 'available' ? '用途副本已通过审核并可用于当前 Provider。'
      : '审核已通过，当前用途副本仍不可用。',
    rejected: '用途审核未通过，请联系获准支持人员查看。',
    revoked: '用途授权已撤回，请联系获准支持人员查看。',
    disposed: '用途副本已处置，需要获准支持人员重新处理。',
    failed: '用途处理未完成，请联系获准支持人员查看。',
  };
  return descriptions[option.purpose_state];
}

export function VoiceCandidateReview({ text, onApply, onCancel, onSaved, persistRecord = false, ui2 = false, source }: {
  text: string;
  csrfToken?: string;
  onApply?: (value: ConfirmedVoice) => void;
  onCancel: () => void;
  onSaved?: (recordVersionId: string) => void;
  persistRecord?: boolean;
  ui2?: boolean;
  source?: VoiceTranscriptSource;
}) {
  const { patientId } = useAccess();
  const [options, setOptions] = useState<VoiceInputOptions | null>(null);
  const [confirmedText, setConfirmedText] = useState(text);
  const [sourceRevisionId, setSourceRevisionId] = useState('');
  const [uploadingOriginal, setUploadingOriginal] = useState(false);
  const [sourceStatus, setSourceStatus] = useState<string | null>(null);
  const [selectedOriginalId, setSelectedOriginalId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<'select' | 'processing' | 'review' | 'saved'>('select');
  const [voiceInput, setVoiceInput] = useState<VoiceInputView | null>(null);
  const [temperature, setTemperature] = useState('');
  const [unit, setUnit] = useState<'C' | 'F' | ''>('');
  const [symptoms, setSymptoms] = useState<string[]>([]);
  const [note, setNote] = useState('');
  const [observedTime, setObservedTime] = useState<TimeInput>(emptyTime);
  const [timeField, setTimeField] = useState<'temp' | 'observation_note'>('observation_note');
  const [decisions, setDecisions] = useState<Record<DecisionRow['field_path'], DecisionRow['decision']>>({
    temperature: 'unknown', temperature_unit: 'unknown', symptoms: 'unknown', observed_time: 'unknown', note: 'unknown',
  });
  const [saving, setSaving] = useState(false);
  const originalOptions = useMemo(() => options?.original_options.filter(item => item.confirmed_text === confirmedText
      && (!source?.transcriptionAuthorizationId || options.source_options.some(option => option.source_revision_id === item.source_revision_id
      && option.family_authorization_id === source.transcriptionAuthorizationId))), [options, confirmedText, source]);
  const selectedOriginal = originalOptions?.find(item => item.original_file_id === selectedOriginalId);
  const sourceOption = options?.source_options.find(item => item.source_revision_id === selectedOriginal?.source_revision_id);
  const uploadSourceOptions = options?.source_options.filter(item => !source?.transcriptionAuthorizationId
    || item.family_authorization_id === source.transcriptionAuthorizationId) ?? [];
  const selectedUploadSource = uploadSourceOptions.find(item => item.source_revision_id === sourceRevisionId);
  const candidates = voiceInput?.candidates;

  useEffect(() => {
    const request = new AbortController();
    setOptions(null); setError(null); setSourceStatus(null); setConfirmedText(text); setSelectedOriginalId(''); setSourceRevisionId('');
    if (!patientId) { setError('当前没有选中的患儿。'); return () => request.abort(); }
    void readVoiceInputOptions(patientId, request.signal).then(value => {
      if (request.signal.aborted) return;
      if (value.patient_id !== patientId) throw new Error('语音选项与当前患儿不一致');
      setOptions(value);
      const matching = value.original_options.filter(item => item.confirmed_text === text && item.state === 'available'
        && (!source?.transcriptionAuthorizationId || value.source_options.some(option => option.source_revision_id === item.source_revision_id
          && option.family_authorization_id === source.transcriptionAuthorizationId)));
      if (matching.length === 1) setSelectedOriginalId(matching[0].original_file_id);
      const sources = value.source_options.filter(item => !source?.transcriptionAuthorizationId
        || item.family_authorization_id === source.transcriptionAuthorizationId);
      if (sources.length === 1) setSourceRevisionId(sources[0].source_revision_id);
    }, failure => { if (!request.signal.aborted) setError(failure.message); });
    return () => request.abort();
  }, [patientId, text, source?.transcriptionAuthorizationId]);

  async function refreshSourceOptions() {
    if (!patientId) throw new Error('当前没有选中的患儿。');
    const value = await readVoiceInputOptions(patientId);
    if (value.patient_id !== patientId) throw new Error('语音来源与当前患儿不一致。');
    setOptions(value);
    const matching = value.original_options.filter(item => item.confirmed_text === confirmedText
      && (!source?.transcriptionAuthorizationId || value.source_options.some(option => option.source_revision_id === item.source_revision_id
        && option.family_authorization_id === source.transcriptionAuthorizationId)));
    if (matching.length === 1) setSelectedOriginalId(matching[0].original_file_id);
    setSourceStatus(matching.length === 1
      ? `已重新读取：${purposeStateDescription(matching[0])}`
      : matching.length > 1 ? '找到多个文字一致的原件，请在列表中选择要查看状态的原件。'
        : '未找到与当前确认文字匹配的原件；请重新读取或保存确认文字原件。');
  }

  async function saveTextOriginal() {
    if (!patientId || !selectedUploadSource || uploadingOriginal) return;
    setUploadingOriginal(true); setError(null); setSourceStatus(null);
    try {
      const saved = await saveConfirmedVoiceText(patientId, selectedUploadSource.source_revision_id, confirmedText,
        undefined, new AbortController().signal);
      const value = await readVoiceInputOptions(patientId);
      const rereadOriginal = value.original_options.find(item => item.original_file_id === saved.file_id
          && item.checksum === saved.checksum && item.confirmed_text === confirmedText
          && item.source_revision_id === saved.source_revision_id);
      if (value.patient_id !== patientId || !rereadOriginal) {
        throw new Error('确认文字上传后未从语音选项重新读取到同一原件及用途审核状态。');
      }
      setOptions(value); setSelectedOriginalId(saved.file_id);
      setSourceStatus(`确认文字原件已保存并重新读取；${purposeStateDescription(rereadOriginal)}`);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '确认文字原件保存或重新读取失败。');
    } finally { setUploadingOriginal(false); }
  }

  function setDecision(path: DecisionRow['field_path'], value: DecisionRow['decision']) {
    setDecisions(current => ({ ...current, [path]: value }));
  }

  function applyCandidates(view: VoiceInputView) {
    if (!view.candidates) throw new Error('VoiceInput 没有已保存的候选字段');
    setTemperature(view.candidates.temperature === null ? '' : String(view.candidates.temperature));
    setUnit(view.candidates.temperature_unit ?? '');
    setSymptoms(view.candidates.symptoms);
    setNote(view.candidates.note ?? '');
    if (view.candidates.temperature !== null) setDecision('temperature', 'accepted');
    if (view.candidates.temperature_unit !== null) setDecision('temperature_unit', 'accepted');
    if (view.candidates.symptoms.length) setDecision('symptoms', 'accepted');
    if (view.candidates.observed_time) setDecision('observed_time', 'unknown');
    if (view.candidates.note) setDecision('note', 'accepted');
    setVoiceInput(view);
    setStage('review');
  }

  async function readByRequest(patientId: string, requestId: string): Promise<VoiceInputView> {
    return await lookupVoiceInput(patientId, requestId);
  }

  async function readVoice(patientId: string, voiceId: string): Promise<VoiceInputView> {
    const value = await readVoiceInput(patientId, voiceId);
    if (value.id !== voiceId) throw new Error('服务返回的 VoiceInput 与本次请求不一致');
    return value;
  }

  async function process() {
    if (!selectedOriginal || selectedOriginal.state !== 'available' || selectedOriginal.purpose_state !== 'approved'
      || !selectedOriginal.purpose_copy_id || !sourceOption || !options?.field_config_id || !options.provider_config_id) {
      setError('当前原文、用途副本或字段配置没有可用批准版本。'); return;
    }
    const patientId = options.patient_id;
    const purposeCopyId = selectedOriginal.purpose_copy_id;
    const fieldConfigId = options.field_config_id;
    const providerConfigId = options.provider_config_id;
    if (!purposeCopyId || !fieldConfigId || !providerConfigId) throw new Error('语音用途副本或配置标识缺失');
    const textDigest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(confirmedText));
    const textHash = Array.from(new Uint8Array(textDigest), byte => byte.toString(16).padStart(2, '0')).join('');
    const key = `sjia.voice-input.${patientId}.${textHash}`;
    const remembered = sessionStorage.getItem(key);
    const requestId = remembered ? (JSON.parse(remembered) as { requestId: string }).requestId : crypto.randomUUID();
    setError(null); setStage('processing');
    let current: VoiceInputView;
    try {
      if (remembered) {
        current = await readByRequest(patientId, requestId);
        if (current.original_file_id !== selectedOriginal.original_file_id || current.transcript !== confirmedText) {
          throw new Error('已存在的 VoiceInput 与当前确认原文原件不一致。');
        }
      } else {
        sessionStorage.setItem(key, JSON.stringify({ requestId, originalFileId: selectedOriginal.original_file_id }));
        const created = await createVoiceInput(patientId, { request_id: requestId, original_file_id: selectedOriginal.original_file_id,
            authorization_id: sourceOption.external_authorization_id, field_config_id: fieldConfigId,
            confirmed_text: confirmedText, transcript_confirmed: true, input_mode: source?.inputMode ?? 'SIMULATION_TEXT_ONLY',
            transcription_evidence_id: source?.transcriptionEvidenceId ?? null });
        current = await readVoiceInput(patientId, created.id);
        if (current.id !== created.id || current.transcript !== confirmedText || current.request_id !== requestId) {
          throw new Error('VoiceInput 保存后重新读取的原文或请求标识不一致');
        }
      }
      if (current.status === 'ready') {
        current = await processVoiceInput(patientId, current.id, { expected_status_version: current.status_version,
          purpose_copy_id: purposeCopyId, provider_config_id: providerConfigId });
      }
      if (current.status === 'saved' && current.saved_record_version_id) {
        setVoiceInput(current); setStage('saved'); onSaved?.(current.saved_record_version_id); return;
      }
      if (current.status === 'awaiting_confirmation' && current.candidates) { applyCandidates(current); return; }
      if (!current.task_id) throw new Error('VoiceInput 尚未关联任务，服务端未提供可读取任务。');
      for (let count = 0; count < 45; count++) {
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        current = await readVoice(patientId, current.id);
        if (current.status === 'awaiting_confirmation' && current.candidates) { applyCandidates(current); return; }
        if (['failed', 'uncertain', 'cancelled', 'revoked'].includes(current.status)) {
          throw new Error(`语音候选任务状态：${current.status}`);
        }
      }
      setVoiceInput(current);
      setError('任务仍在运行。再次读取原 VoiceInput 可继续查看状态，页面不会重复提交模型任务。');
      setStage('select');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '语音候选任务无法读取');
      setStage('select');
    }
  }

  function buildDecisions(): DecisionRow[] {
    if (!candidates) throw new Error('缺少候选数据');
    const temperatureValue = temperature.trim();
    const tempPath = (path: 'temperature' | 'temperature_unit'): DecisionRow => {
      const decision = decisions[path];
      const selected = path === 'temperature' ? temperatureValue : unit;
      return { field_path: path, decision,
        selected_value: decision === 'accepted' || decision === 'modified' ? selected : null,
        record_field_codes: decision === 'excluded' ? [] : ['temp'] };
    };
    const time = timePayload(observedTime);
    const timeValue = time;
    return [tempPath('temperature'), tempPath('temperature_unit'),
      { field_path: 'symptoms', decision: decisions.symptoms,
        selected_value: decisions.symptoms === 'accepted' || decisions.symptoms === 'modified' ? symptoms : null,
        record_field_codes: decisions.symptoms === 'excluded' ? [] : ['observation_note'] },
      { field_path: 'observed_time', decision: decisions.observed_time,
        selected_value: decisions.observed_time === 'accepted' || decisions.observed_time === 'modified' ? timeValue : null,
        record_field_codes: decisions.observed_time === 'excluded' ? [] : [timeField] },
      { field_path: 'note', decision: decisions.note,
        selected_value: decisions.note === 'accepted' || decisions.note === 'modified' ? note : null,
        record_field_codes: decisions.note === 'excluded' ? [] : ['observation_note'] },
    ];
  }

  async function adopt() {
    if (!voiceInput || !candidates || !options) throw new Error('缺少待采用的候选结果');
    setSaving(true); setError(null);
    try {
      const rows = buildDecisions();
      if (rows.some(row => (row.decision === 'accepted' || row.decision === 'modified') && row.selected_value === null)) {
        throw new Error('已采用字段需要填写本人确认值');
      }
      if ((decisions.symptoms === 'accepted' || decisions.symptoms === 'modified') && symptoms.length === 0) {
        throw new Error('已采用的症状候选不能为空');
      }
      if ((decisions.note === 'accepted' || decisions.note === 'modified') && !note.trim()) {
        throw new Error('已采用的补充说明不能为空');
      }
      if (decisions.observed_time === 'accepted' && observedTime.precision !== 'unknown'
          && candidates.observed_time !== observedTime.value) {
        throw new Error('本人调整了时间内容，请将时间决定设为“修改后采用”。');
      }
      const selectedTemperature = rows.find(row => row.field_path === 'temperature')?.selected_value;
      const selectedUnit = rows.find(row => row.field_path === 'temperature_unit')?.selected_value;
      const selectedSymptoms = rows.find(row => row.field_path === 'symptoms')?.selected_value;
      const selectedNote = rows.find(row => row.field_path === 'note')?.selected_value;
      const selectedTime = rows.find(row => row.field_path === 'observed_time')?.selected_value;
      const value: ConfirmedVoice = {
        temperature: selectedTemperature !== null && selectedTemperature !== undefined && selectedUnit
          ? { value: temperature.trim(), unit } : undefined,
        time: selectedTime && observedTime.precision !== 'unknown' ? observedTime : undefined,
        timeField,
        description: [...(Array.isArray(selectedSymptoms) ? selectedSymptoms : []),
          typeof selectedNote === 'string' ? selectedNote : ''].filter(Boolean).join('\n'),
      };
      if (!persistRecord) {
        if (!onApply) throw new Error('当前表单没有接收本人确认字段的入口。');
        const current = await readVoiceInput(options.patient_id, voiceInput.id);
        if (current.status !== 'awaiting_confirmation' || current.status_version !== voiceInput.status_version) {
          throw new Error('候选状态已经改变，请重新读取后核对。');
        }
        const cancelled = await cancelVoiceInput(options.patient_id, current.id, current.status_version);
        const reread = await readVoiceInput(options.patient_id, current.id);
        if (cancelled.status !== 'cancelled' || reread.status !== 'cancelled'
            || reread.status_version !== cancelled.status_version || reread.saved_record_version_id !== null) {
          throw new Error('候选处理取消后的状态尚未核验，当前表单没有采用候选。');
        }
        onApply(value);
        onCancel();
        return;
      }
      const receipt: SavedVoiceResult = await adoptVoiceInput(options.patient_id, voiceInput.id,
        { voice_input_id: voiceInput.id, expected_status_version: voiceInput.status_version, decisions: rows });
      const savedVoice = await readVoiceInput(options.patient_id, voiceInput.id);
      if (savedVoice.id !== voiceInput.id || savedVoice.saved_record_version_id !== receipt.record.record_version_id) {
        throw new Error('VoiceInput 与保存后 RecordVersion 的回执不一致');
      }
      onApply?.(value);
      onSaved?.(receipt.record.record_version_id);
      setVoiceInput(savedVoice); setStage('saved');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '保存语音候选失败');
    } finally { setSaving(false); }
  }

  async function cancel() {
    try {
      if (voiceInput && options && !['saved', 'cancelled', 'revoked'].includes(voiceInput.status)) {
        const cancelled = await cancelVoiceInput(options.patient_id, voiceInput.id, voiceInput.status_version);
        if (!['cancelled', 'uncertain', 'failed'].includes(cancelled.status)) {
          throw new Error(`取消回执状态需要核查：${cancelled.status}`);
        }
      }
      onCancel();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '取消语音任务失败');
    }
  }

  function renderDecision(path: DecisionRow['field_path'], label: string) {
    return <label className="field"><span>{label}决定</span><select value={decisions[path]}
      disabled={saving || stage === 'saved'} onChange={event => setDecision(path, event.target.value as DecisionRow['decision'])}>
      {decisionLabels.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
    </select></label>;
  }

  return <section className={ui2 ? 'card form-card' : 'field-group'} aria-label="语音候选核对">
    <h2>核对语音候选</h2>
    <p className="field-hint">候选只会在模型任务完成并重新读取 VoiceInput 后显示。每项决定会与 RecordVersion 回执共同保存。</p>
    {stage === 'select' ? <label className="field"><span>本人确认的完整文字</span><textarea aria-label="本人确认的完整文字" value={confirmedText}
      maxLength={8000} disabled={uploadingOriginal} onChange={event => {
        setConfirmedText(event.target.value); setSelectedOriginalId(''); setSourceStatus(null);
      }} /></label> : <details><summary>查看本人确认的原始描述</summary><p>{confirmedText}</p></details>}
    {!options && !error && <p role="status">正在读取语音来源和用途配置。</p>}
    {options?.state === 'unavailable' && <p role="alert">{options.reason_code ?? '当前语音用途不可用。'}</p>}
    {options && stage === 'select' && <>
      {uploadSourceOptions.length > 0 && <><label className="field"><span>文字原件来源</span><select value={sourceRevisionId}
        onChange={event => setSourceRevisionId(event.target.value)} disabled={uploadingOriginal}>
        <option value="">请选择获准来源</option>{uploadSourceOptions.map(item => <option key={item.source_revision_id} value={item.source_revision_id}>
          来源版本 {item.source_revision_id}
        </option>)}
      </select></label>
      <button type="button" className={ui2 ? 'button secondary' : 'action secondary'} disabled={!selectedUploadSource
        || !confirmedText.trim() || confirmedText.length > 8000 || uploadingOriginal}
        onClick={() => void saveTextOriginal()}>{uploadingOriginal ? '正在保存并重新读取文字原件' : '保存已确认文字原件'}</button></>}
      <label className="field"><span>已保存的确认文字原件</span><select aria-label="已保存的确认文字原件" value={selectedOriginalId}
        onChange={event => setSelectedOriginalId(event.target.value)}>
        <option value="">请选择与本人确认文字完全一致的原件</option>
        {originalOptions?.map(item => <option key={item.original_file_id} value={item.original_file_id}>
          {purposeStateLabels[item.purpose_state]} · {item.original_file_id}
        </option>)}
      </select></label>
      {originalOptions?.length === 0 && <p role="status">还没有与本人确认文字完全一致的原件。保存文字原件后可查看用途审核状态。</p>}
      {selectedOriginal && <div role="status" aria-live="polite">
        <p>{purposeStateDescription(selectedOriginal)}</p>
        {selectedOriginal.purpose_reason_code && <p>原因代码：{selectedOriginal.purpose_reason_code}</p>}
        {selectedOriginal.purpose_request_id && <p>用途请求标识：{selectedOriginal.purpose_request_id}</p>}
        {selectedOriginal.purpose_task_id && <p>用途处理任务标识：{selectedOriginal.purpose_task_id}</p>}
        {selectedOriginal.purpose_copy_id && <p>用途副本标识：{selectedOriginal.purpose_copy_id}</p>}
      </div>}
      {sourceStatus && <p role="status">{sourceStatus}</p>}
      {selectedOriginal && selectedOriginal.state !== 'available' && <a className="text-action"
        href={`/support/data-use?file_id=${encodeURIComponent(selectedOriginal.original_file_id)}&purpose=external_recognition`}>
        打开获准支持人员的用途处理页面
      </a>}
      {selectedOriginal && selectedOriginal.state !== 'available' && <button type="button" className="text-action"
        disabled={uploadingOriginal} onClick={() => void refreshSourceOptions()}>重新读取用途处理与审核状态</button>}
      {voiceInput?.task_id && <button type="button" className={ui2 ? 'button secondary' : 'action secondary'}
        onClick={() => void process()}>重新读取原任务</button>}
      <button type="button" className={ui2 ? 'button' : 'action'} disabled={!selectedOriginal || selectedOriginal.state !== 'available'
        || selectedOriginal.purpose_state !== 'approved' || !selectedOriginal.purpose_copy_id || saving}
        onClick={() => void process()}>提取候选并保存任务</button>
    </>}
    {stage === 'processing' && <p role="status">语音任务正在执行；页面按原 VoiceInput 读取状态。</p>}
    {stage === 'review' && candidates && <>
      <label className={ui2 ? 'checkbox-row' : 'confirmation-check'}>体温候选</label>
      {renderDecision('temperature', '体温')}
      <label className="field"><span>候选体温</span><input inputMode="decimal" value={temperature} onChange={event => setTemperature(event.target.value)} /></label>
      {renderDecision('temperature_unit', '体温单位')}
      <label className="field"><span>候选单位</span><select value={unit} onChange={event => setUnit(event.target.value as 'C' | 'F' | '')}><option value="">未知</option><option value="C">摄氏度（°C）</option><option value="F">华氏度（°F）</option></select></label>
      {renderDecision('symptoms', '症状')}
      <label className="field"><span>候选症状</span><textarea value={symptoms.join('\n')} onChange={event => setSymptoms(event.target.value.split('\n').filter(Boolean))} /></label>
      {renderDecision('observed_time', '发生时间')}
      <p>模型原始时间描述：{candidates.observed_time ?? '无法确定'}</p>
      <label className="field"><span>时间字段</span><select value={timeField} onChange={event => setTimeField(event.target.value as 'temp' | 'observation_note')}><option value="observation_note">症状说明</option><option value="temp">体温</option></select></label>
      {decisions.observed_time === 'accepted' || decisions.observed_time === 'modified'
        ? <MedicalTimeInput ui2={ui2} label="本人确认的发生时间" value={observedTime} onChange={setObservedTime} precisionDetails />
        : null}
      {renderDecision('note', '补充说明')}
      <label className="field"><span>候选补充说明</span><textarea value={note} onChange={event => setNote(event.target.value)} /></label>
      <p className="field-hint">模型候选与本人决定均按当前版本保存。时间无法确认时保留未知；不确定项不会写成阴性。</p>
      <button type="button" className={ui2 ? 'button' : 'action'} disabled={saving} onClick={() => void adopt()}>{saving ? '正在保存并重新读取' : persistRecord ? '确认本人决定并保存观察记录' : '确认候选并加入当前表单'}</button>
    </>}
    {stage === 'saved' && voiceInput?.saved_record_version_id && <p role="status">语音观察记录已保存并重新读取。RecordVersion：{voiceInput.saved_record_version_id}</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
    <button type="button" className="text-action" onClick={() => void cancel()}>{stage === 'saved' ? '完成' : '取消本次提取'}</button>
  </section>;
}
