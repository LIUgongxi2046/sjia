import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { CheckCircle2, CircleAlert, LockKeyhole, ShieldCheck } from 'lucide-react';
import { UI2Frame } from '../../components/UI2Frame';
import { ApiError, fetchPrivateImage } from '../../api/client';
import { readPurposeCopy, readPurposeEvidence, readPurposeTask, preparePurposeRequest, submitPurposeRequest, readPurposeRequest, retryPurposeRequest } from '../../api/purpose';
import type { PurposeRequestIdentity } from '../../api/purpose';
import { validateApiValue } from '../../api/schema';
import type { IntentView } from '../../../../frontend-design/types';
import type { Purpose, PurposeCopyEvidenceView, PurposeCopyInput, PurposeCopyOptionsView, PurposeCopyView, PurposeReviewInput, SupportSessionView, WriteReceipt } from '../../../../frontend-design/v2-types';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';
import { PurposeTaskProgress } from './PurposeTaskProgress';
import { PurposeCopyCreation } from './PurposeCopyCreation';
import { DispositionPanel } from './DispositionPanel';

const purposes: [Purpose, string][] = [['external_recognition', '外部识别'], ['format_evaluation', '功能测试'],
  ['clinical_evaluation', '临床评价'], ['competition_display', '比赛展示'], ['export', '获准导出']];
const scopes: [PurposeReviewInput['scope_results'][number]['scope'], string][] = [['image_text', '图片文字'],
  ['confirmed_text', '确认文字'], ['model_input', '模型输入'], ['log', '日志'], ['presentation', '展示材料']];

function Pair({ label, children }: { label: string; children: ReactNode }) {
  return <div className="pair"><span>{label}</span><strong>{children}</strong></div>;
}

export function PurposeCopyPage({ session, accessState, accessError, onRefresh, onLogout }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null;
  onRefresh: () => void; onLogout: () => void;
}) {
  const navigate = useNavigate();
  const [parameters, setParameters] = useSearchParams();
  const copyId = parameters.get('copy_id');
  const intentId = parameters.get('intent_id');
  const taskId = parameters.get('task_id');
  const fileId = parameters.get('file_id');
  const [copy, setCopy] = useState<PurposeCopyView | null>(null);
  const [evidence, setEvidence] = useState<PurposeCopyEvidenceView | null>(null);
  const [creationOptions, setCreationOptions] = useState<PurposeCopyOptionsView | null>(null);
  const [readState, setReadState] = useState<SupportReadState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<WriteReceipt | null>(null);
  const [phase, setPhase] = useState<'ready' | 'pending' | 'failed' | 'saved'>('ready');
  const [mayRetry, setMayRetry] = useState(false);
  const [purpose, setPurpose] = useState<Purpose | ''>(() => purposes.find(([value]) => value === parameters.get('purpose'))?.[0] ?? '');
  const [mode, setMode] = useState<'review' | 'revoke' | null>(null);
  const [input, setInput] = useState<Record<string, string>>({});
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imagePending, setImagePending] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const imageDialog = useRef<HTMLDialogElement>(null);
  const imageController = useRef<AbortController>();
  const currentImage = useRef<string | null>(null);
  const composing = useRef(false);
  const controller = useRef<AbortController>();
  const writeController = useRef<AbortController>();
  const generation = useRef(0);
  const inFlight = useRef(false);
  const preparing = useRef<PurposeRequestIdentity | null>(null);
  const prepared = useRef<{ identity: PurposeRequestIdentity; intent: IntentView } | null>(null);
  const available = accessState === 'default' && session !== null && session.capabilities.some(item => item.capability_id === 'manage_data_use' && item.state === 'available');
  const taskCompleted = useCallback((id: string) => {
    if (copyId === id) return;
    setParameters({ copy_id: id, ...(taskId ? { task_id: taskId } : {}) }, { replace: true });
  }, [copyId, setParameters, taskId]);

  function releaseImage() {
    imageController.current?.abort();
    if (currentImage.current) URL.revokeObjectURL(currentImage.current);
    currentImage.current = null; setImageUrl(null); setImagePending(false);
  }

  const readCopy = useCallback(async (id: string, signal: AbortSignal) => {
    const [current, proof] = await Promise.all([readPurposeCopy(id, signal), readPurposeEvidence(id, signal)]);
    if (current.copy_id !== proof.copy_id || current.version !== proof.version) throw new Error('资料版本已经变化，请重新读取。');
    return { current, proof };
  }, []);

  const refreshCopy = useCallback(async () => {
    const currentGeneration = ++generation.current;
    controller.current?.abort(); controller.current = new AbortController();
    releaseImage();
    setCopy(null); setEvidence(null); setReceipt(null); setError(null); setReadState('loading');
    if (!available) return;
    if (!copyId) { setReadState('default'); return; }
    try {
      const result = await readCopy(copyId, controller.current.signal);
      if (currentGeneration !== generation.current) return;
      setCopy(result.current); setEvidence(result.proof); setPurpose(result.current.purpose); setReadState('default');
    } catch (failure) {
      if (currentGeneration !== generation.current || controller.current.signal.aborted) return;
      setReadState(failure instanceof ApiError && [401, 403].includes(failure.status) ? 'permission' : navigator.onLine ? 'error' : 'offline');
      setError(failure instanceof Error ? failure.message : '资料读取失败。');
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) onRefresh();
    }
  }, [available, copyId, onRefresh, readCopy]);

  useEffect(() => {
    void refreshCopy();
    return () => { generation.current += 1; controller.current?.abort(); writeController.current?.abort();
      imageController.current?.abort(); if (currentImage.current) URL.revokeObjectURL(currentImage.current); currentImage.current = null; };
  }, [refreshCopy]);
  useEffect(() => {
    if (mode) dialog.current?.showModal();
  }, [mode]);
  useEffect(() => {
    if (!available) { dialog.current?.close(); setMode(null); setCopy(null); setEvidence(null); }
  }, [available]);
  useEffect(() => { if (imageUrl) imageDialog.current?.showModal(); }, [imageUrl]);

  async function openImage(reviewOnly: boolean) {
    if (!copy || !available || imagePending) return;
    releaseImage(); setImagePending(true); setError(null);
    imageController.current = new AbortController();
    const signal = imageController.current.signal;
    try {
      const blob = await fetchPrivateImage(`/api/v2/support/purpose-copies/${copy.copy_id}/${reviewOnly ? 'review-content' : 'content'}`, signal);
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), value => value.toString(16).padStart(2, '0')).join('');
      if (hash !== copy.checksum) throw new Error('副本内容已经变化，请重新读取版本。');
      if (signal.aborted) return;
      const url = URL.createObjectURL(blob); currentImage.current = url; setImageUrl(url);
    } catch (failure) {
      if (!signal.aborted) {
        setError(failure instanceof Error ? failure.message : '副本内容读取失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) onRefresh();
      }
    } finally { if (!signal.aborted) setImagePending(false); }
  }

  async function adoptProviderCopyReceipt(saved: WriteReceipt, signal: AbortSignal) {
    if (!copyId || saved.resource_type !== 'provider_copy') throw new Error('模拟提供方副本回执身份不一致。');
      const result = await readCopy(copyId, signal);
      if (!result.proof.simulated_provider_copies.some(item => item.provider_copy_id === saved.resource_id)) throw new Error('模拟提供方副本保存后未能重新读取。');
      if (signal.aborted) return;
      setCopy(result.current); setEvidence(result.proof); setReceipt(saved); setPhase('saved'); setReadState('default');
      prepared.current = null; preparing.current = null; return;
  }

  async function adoptReceipt(saved: WriteReceipt, signal: AbortSignal) {
    if (saved.resource_type === 'provider_copy' && prepared.current?.identity.operation === 'simulation_provider_copy_create') {
      await adoptProviderCopyReceipt(saved, signal);
      return;
    }
    if (!copyId && fileId && saved.resource_type === 'task') {
      if (saved.task_id !== saved.resource_id || saved.version !== '1') throw new Error('保存结果缺少准确处理任务。');
      const task = await readPurposeTask(saved.resource_id, signal);
      if (task.task_id !== saved.resource_id || task.task_type !== 'purpose_copy') throw new Error('保存结果与当前处理任务不一致。');
      if (signal.aborted) return;
      setReceipt(saved); setPhase('saved'); setMayRetry(false); prepared.current = null; preparing.current = null;
      setParameters(value => { const next = new URLSearchParams(value); next.set('task_id', task.task_id); return next; }, { replace: true });
      return;
    }
    if (!copyId || saved.resource_type !== 'purpose_copy' || saved.resource_id !== copyId) throw new Error('提交结果与当前资料身份不一致。');
    const result = await readCopy(copyId, signal);
    if (result.current.version !== saved.version) throw new Error('保存事实已经记录，当前资料版本需要重新核查。');
    if (signal.aborted) return;
    releaseImage();
    setCopy(result.current); setEvidence(result.proof); setReceipt(saved); setPhase('saved'); setReadState('default');
    setMayRetry(false); prepared.current = null; preparing.current = null; setInput({});
  }

  function saveIntentParameter(id: string) {
    setParameters(value => { const next = new URLSearchParams(value); next.set('intent_id', id);
      if (!copyId && purpose) next.set('purpose', purpose); return next; }, { replace: true });
  }

  async function queryRequest() {
    if ((!intentId && !preparing.current) || inFlight.current || !available) return;
    inFlight.current = true; setPhase('pending'); setError(null);
    writeController.current?.abort(); writeController.current = new AbortController();
    const signal = writeController.current.signal;
    try {
      if (!intentId && preparing.current) {
        const intent = await preparePurposeRequest(preparing.current, signal);
        prepared.current = { identity: preparing.current, intent };
        if (!copyId && !fileId) throw new Error('当前资料身份尚未确定。');
        saveIntentParameter(intent.intent_id);
        await execute(prepared.current.identity, intent, signal);
        return;
      }
      if (!intentId) throw new Error('当前请求身份尚未确定。');
      const result = await readPurposeRequest(intentId, signal);
      if (signal.aborted) return;
      const target = copyId ? `/support/purpose-copies/${copyId}/${result.operation_id === 'purpose_copy_review' ? 'reviews' : result.operation_id === 'simulation_provider_copy_create' ? 'simulation-provider-copy' : 'revoke'}` : `/support/files/${fileId}/purpose-copies`;
      if (result.target_key !== target || (result.operation_id === 'purpose_copy_create') !== (!copyId && fileId !== null)) throw new Error('请求范围与当前资料不一致。');
      if (result.state === 'committed') {
        if (!result.receipt) throw new Error('保存结果缺少准确资料引用。');
        if (result.operation_id === 'simulation_provider_copy_create') await adoptProviderCopyReceipt(result.receipt, signal);
        else await adoptReceipt(result.receipt, signal);
      } else {
        setMayRetry(result.state === 'failed' && result.may_retry && prepared.current !== null);
        setPhase(result.state === 'failed' ? 'failed' : 'pending');
        setError(result.state === 'failed' ? '提交未完成，请核查输入和当前资料。' : null);
      }
    } catch (failure) {
      if (!signal.aborted) {
        setError(failure instanceof Error ? failure.message : '提交结果读取失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) onRefresh();
      }
    } finally { inFlight.current = false; }
  }

  useEffect(() => {
    if (intentId && available && readState === 'default' && phase === 'ready') void queryRequest();
  }, [intentId, available, readState, phase]);

  async function execute(identity: PurposeRequestIdentity, intent: IntentView, signal: AbortSignal) {
    const saved = await submitPurposeRequest(identity, intent, signal);
    await adoptReceipt(saved, signal);
    dialog.current?.close(); setMode(null);
  }

  async function submitCreation(payload: PurposeCopyInput) {
    if (!fileId || copyId || inFlight.current || phase === 'pending' || taskId) return;
    const identity: PurposeRequestIdentity = { requestId: crypto.randomUUID(), operation: 'purpose_copy_create',
      target: `/support/files/${fileId}/purpose-copies`, payload };
    await persist(identity, 'PurposeCopyInput');
  }

  async function registerSimulationProviderCopy() {
    if (!copy || !copy.review_id || inFlight.current || phase === 'pending' || !available) return;
    const identity: PurposeRequestIdentity = { requestId: crypto.randomUUID(), operation: 'simulation_provider_copy_create',
      target: `/support/purpose-copies/${copy.copy_id}/simulation-provider-copy`,
      payload: { expected_version: copy.version, review_id: copy.review_id } };
    await persist(identity, 'PurposeProviderCopyInput');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!copy || !mode || inFlight.current || composing.current || phase === 'pending') return;
    const operation = mode === 'review' ? 'purpose_copy_review' : 'purpose_copy_revoke';
    const payload = mode === 'review' ? {
      expected_version: copy.version, checksum: copy.checksum,
      scope_results: scopes.map(([scope]) => ({ scope, result: input[scope] })),
      clinical_integrity: input.clinical_integrity, date_shift_consistency: input.date_shift_consistency, evidence_ref: input.evidence_ref,
    } : { expected_version: copy.version, reason: input.reason };
    const identity: PurposeRequestIdentity = { requestId: crypto.randomUUID(), operation,
      target: `/support/purpose-copies/${copy.copy_id}/${mode === 'review' ? 'reviews' : 'revoke'}`, payload: payload as PurposeRequestIdentity['payload'] };
    await persist(identity, mode === 'review' ? 'PurposeReviewInput' : 'PurposeRevokeInput');
  }

  async function persist(identity: PurposeRequestIdentity, schema: string) {
    inFlight.current = true; setDialogError(null); setPhase('pending'); setMayRetry(false);
    prepared.current = null; preparing.current = null;
    writeController.current?.abort(); writeController.current = new AbortController();
    const signal = writeController.current.signal;
    try {
      validateApiValue(`v2-protocol.schema.json#/$defs/${schema}`, identity.payload);
      preparing.current = identity;
      const intent = await preparePurposeRequest(identity, signal);
      prepared.current = { identity, intent };
      saveIntentParameter(intent.intent_id);
      await execute(identity, intent, signal);
    } catch (failure) {
      if (!signal.aborted) {
        setDialogError(failure instanceof Error ? failure.message : '提交状态需要重新读取。');
        if (prepared.current === null && !(failure instanceof TypeError)) setPhase('failed');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) onRefresh();
      }
    } finally { inFlight.current = false; }
  }

  async function retry() {
    if (!mayRetry || !prepared.current || inFlight.current) return;
    inFlight.current = true; setPhase('pending'); setMayRetry(false);
    writeController.current?.abort(); writeController.current = new AbortController();
    const signal = writeController.current.signal;
    try {
      const intent = await retryPurposeRequest(prepared.current.intent, signal);
      prepared.current = { identity: prepared.current.identity, intent };
      await execute(prepared.current.identity, intent, signal);
    } catch (failure) {
      if (!signal.aborted) setError(failure instanceof Error ? failure.message : '重试结果需要查询。');
    } finally { inFlight.current = false; }
  }

  const state = accessState !== 'default' ? accessState : !available ? 'permission' : readState;
  const stale = evidence !== null && (evidence.restriction_reasons.length > 0 || ['revoked', 'disposed'].includes(copy?.status ?? '') || (copy?.status === 'approved' && copy.review_id === null));
  const selected = copy !== null && purpose === copy.purpose;
  const originalSelection = !copyId && creationOptions?.original_file_id === fileId && creationOptions?.purpose === purpose ? creationOptions : null;
  const field = (name: string, label: string, choices: [string, string][]) => <label className="field"><span>{label}</span>
    <select required value={input[name] ?? ''} onChange={event => setInput(value => ({ ...value, [name]: event.target.value }))}>
      <option value="" disabled>请选择</option>{choices.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
    </select></label>;

  return <UI2Frame title="用途与副本" screenId="UI2-38" note="必要副本处理及人工复核在外发前完成。" onBack={() => navigate('/account/data-use')}>
    <div className="support-banner"><LockKeyhole size={16} />受控支持 · 需独立岗位授权</div>
    {accessState === 'default' && session?.capabilities.some(row => row.capability_id === 'decide_relation' && row.state === 'available')
      && <button className="text-action" onClick={() => navigate('/support/applications')}>核验指定患儿关联申请</button>}
    <SupportReadStatus state={state} error={accessError ?? error} onRefresh={state === 'permission' ? () => navigate('/support/login') : () => void refreshCopy()} />
    {taskId && <PurposeTaskProgress taskId={taskId} active={available} onCompleted={taskCompleted} onPermission={onRefresh} />}
    <DispositionPanel active={accessState === 'default' && !!session?.capabilities.some(item => item.capability_id === 'manage_disposition' && item.state === 'available')} onPermission={onRefresh} />
    {state === 'default' && <>
      {stale && <section className="status-panel stale" role="status"><div className="status-top"><CircleAlert size={22} /><span>页面状态</span></div>
        <h2>依据已有更新</h2><p>当前内容需要依据最新版本重新生成。</p><button className="text-action" onClick={() => void refreshCopy()}>重新读取</button></section>}
      {phase === 'pending' && <section className="status-panel pending" role="status"><div className="status-top"><CircleAlert size={22} /><span>页面状态</span></div>
        <h2>保存结果待确认</h2><p>请查询原请求状态，暂时不要重复提交。</p><button className="text-action" onClick={() => void queryRequest()}>查询原请求</button></section>}
      {phase === 'saved' && receipt && <section className="status-panel success" role="status"><div className="status-top"><CheckCircle2 size={22} /><span>页面状态</span></div>
        <h2>{receipt.resource_type === 'task' ? '处理任务已保存' : '记录已保存'}</h2><p>保存时间：{receipt.saved_at}；{receipt.resource_type === 'task' ? '任务版本' : '资料版本'}：{receipt.version}。</p></section>}
      {phase === 'failed' && <section className="status-panel error" role="alert"><h2>提交未完成</h2><p>{error ?? '核查当前资料和输入后继续。'}</p>
        {mayRetry && <button className="text-action" onClick={() => void retry()}>重试原请求</button>}</section>}
      <section className="card form-card"><div className="eyebrow">指定范围内操作</div><h2>用途与副本</h2>
        <p className="muted">必要处理与人工复核完成后才允许外发。</p><Pair label="当前资料范围">{copy?.patient_id ?? originalSelection?.patient_id ?? '尚未授权选择'}</Pair></section>
      <section className="card form-card"><label className="field"><span>目标用途</span><select value={purpose} disabled={phase === 'pending' || !!taskId} onChange={event => setPurpose(event.target.value as Purpose | '')}>
        <option value="" disabled>请选择</option>{purposes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
        <Pair label="原件版本">{selected ? copy.original_version : originalSelection?.original_version ?? '待核查'}</Pair>
        <Pair label="用途副本版本">{selected ? copy.version : '待核查'}</Pair>
        <Pair label="处理证明">{selected && evidence ? evidence.processing_version : '待核查'}</Pair>
        <Pair label="人工复核人">{selected && evidence?.reviewer_id ? evidence.reviewer_id : '待核查'}</Pair>
        <Pair label="临床字段完整性">{selected && evidence ? evidence.clinical_integrity_status === 'verified' && copy.review_id && !stale ? '已复核' : evidence.clinical_integrity_status === 'failed' ? '检查未通过' : '待核查' : '待核查'}</Pair>
      <Pair label="时间关系完整性">{selected && evidence?.date_review && !stale ? { passed: '检查通过', failed: '检查未通过', unknown: '尚未确定', not_applicable: '已记录不适用' }[evidence.date_review] : '待核查'}</Pair>
        {evidence?.simulated_provider_copies.map(item => <Pair key={item.provider_copy_id} label="模拟提供方副本">{item.status} · {item.object_id} · {item.object_version}</Pair>)}
      </section>
      {!copyId && fileId && purpose && <PurposeCopyCreation key={`${fileId}:${purpose}`} fileId={fileId} purpose={purpose}
        active={available} pending={phase === 'pending'} submitted={!!taskId} error={dialogError}
        onSubmit={submitCreation} onPermission={onRefresh} onRead={setCreationOptions} />}
      <button className="button" disabled={!selected || stale || !copy.allowed_actions.includes('review') || phase === 'pending'} onClick={() => { setDialogError(null); setMode('review'); }}>提交复核记录</button>
      {copy && selected && copy.status === 'approved' && evidence?.environment === 'simulation' && evidence.reviewer_id && evidence.simulated_provider_copies.length === 0 && <section className="card form-card">
        <p className="muted">此操作仅登记本地模拟提供方副本，不会连接外部服务。</p>
        <button className="button secondary" disabled={stale || phase === 'pending' || !available} onClick={() => void registerSimulationProviderCopy()}>登记模拟提供方副本</button>
      </section>}
      <div className="note amber"><ShieldCheck size={17} /><p>状态标签本身不能作为去标识化通过证据。</p></div>
      {copy && selected && <section className="card form-card">
        {copy.allowed_actions.includes('review_content') && <button className="text-action" disabled={imagePending} onClick={() => void openImage(true)}>查看待复核副本</button>}
        {copy.allowed_actions.includes('content') && <button className="text-action" disabled={imagePending} onClick={() => void openImage(false)}>查看获准副本</button>}
        <button className="text-action" onClick={() => void refreshCopy()}>重新读取版本与证明</button>
        {copy.allowed_actions.includes('revoke') && <button className="text-action" disabled={phase === 'pending'} onClick={() => { setDialogError(null); setMode('revoke'); }}>撤销用途副本</button>}
      </section>}
      <button className="text-action center" onClick={onLogout}>退出受控会话</button>
    </>}
    {error && state === 'default' && <p role="alert">{error}</p>}
    {available && readState === 'default' && selected && !stale && imageUrl && <dialog ref={imageDialog} className="design-dialog" aria-label="用途副本内容" onClose={releaseImage}>
      <h2>用途副本内容</h2><img src={imageUrl} alt="当前获准用途副本" style={{ width: '100%', objectFit: 'contain' }} />
      <button className="button secondary" onClick={() => imageDialog.current?.close()}>关闭副本</button>
    </dialog>}
    {mode && copy && <dialog ref={dialog} className="design-dialog" aria-label={mode === 'review' ? '提交复核记录' : '撤销用途副本'} onClose={() => setMode(null)}>
      <form onSubmit={event => void submit(event)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
        <h2>{mode === 'review' ? '提交复核记录' : '撤销用途副本'}</h2>
        {mode === 'review' ? <>
          {scopes.map(([scope, label]) => <div key={scope}>{field(scope, label, [['passed', '检查通过'], ['failed', '检查未通过'], ['not_applicable', '不适用']])}</div>)}
          {field('clinical_integrity', '临床字段完整性', [['passed', '检查通过'], ['failed', '检查未通过'], ['unknown', '尚未确定']])}
          {field('date_shift_consistency', '时间关系完整性', [['passed', '检查通过'], ['failed', '检查未通过'], ['unknown', '尚未确定'], ['not_applicable', '不适用']])}
          <label className="field"><span>人工复核证明</span><textarea required value={input.evidence_ref ?? ''} onChange={event => setInput(value => ({ ...value, evidence_ref: event.target.value }))} /></label>
        </> : <label className="field"><span>撤销原因</span><textarea required value={input.reason ?? ''} onChange={event => setInput(value => ({ ...value, reason: event.target.value }))} /></label>}
        {dialogError && <p role="alert">{dialogError}</p>}
        <button className="button" disabled={phase === 'pending'} type="submit">{phase === 'pending' ? '保存结果待确认' : '确认提交'}</button>
        {phase === 'pending' && intentId && <button className="text-action" type="button" onClick={() => void queryRequest()}>查询原请求</button>}
        <button className="button secondary" type="button" onClick={() => dialog.current?.close()}>继续查看</button>
      </form>
    </dialog>}
  </UI2Frame>;
}
