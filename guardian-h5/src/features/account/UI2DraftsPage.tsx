import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ChevronRight as ArrowRight, ArrowDown as ChevronDown, ShieldCheck } from '../../components/ThreeDIcon';
import { FileClock, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2Sheet } from '../../components/UI2Frame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { abandonDraft, type DraftView } from '../../api/endpoints';
import { useFamilyResource } from '../../api/useFamilyResource';

const categories = ['全部', '体温症状', '报告', '治疗', '记录更正'] as const;
const formLabel: Record<DraftView['form_type'], string> = {
  observation: '体温症状', report: '报告', therapy: '治疗', correction: '记录更正',
};

function draftPath(draft: DraftView) {
  if (draft.form_type === 'correction') {
    if (typeof draft.payload.recordId !== 'string') throw new Error('更正草稿缺少原记录。');
    return `/records/${draft.payload.recordId}/correct?draft=${draft.draft_id}`;
  }
  const route = ({ observation: 'observations', report: 'reports', therapy: 'therapies' } as Record<string, string>)[draft.form_type];
  if (!route) throw new Error('草稿表单类型不受支持。');
  return `/register/${route}?draft=${draft.draft_id}`;
}

export function UI2DraftsPage() {
  const access = useAccess();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const requestedId = params.get('draft');
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useFamilyResource<{ drafts: DraftView[] }>(canRead ? `/api/patients/${access.patientId}/drafts` : null, 'DraftList');
  const [category, setCategory] = useState<string>('全部');
  const [target, setTarget] = useState<DraftView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [writeError, setWriteError] = useState<Error | null>(null);
  const request = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const active = access.relations.find(relation => relation.patient_id === access.patientId);
  const close = useCallback(() => {
    if (busy.current) return;
    setTarget(null); setWriteError(null);
  }, []);
  useEffect(() => () => { request.current?.abort(); }, []);
  useEffect(() => {
    if (!canRead) { request.current?.abort(); setTarget(null); setWriteError(null); }
  }, [canRead]);

  async function confirmAbandon() {
    if (!target || !canRead || busy.current) return;
    const controller = new AbortController();
    request.current = controller;
    busy.current = true; setSubmitting(true); setWriteError(null);
    try {
      const result = await abandonDraft(target.draft_id, target.version, access.csrfToken ?? undefined, controller.signal);
      if (controller.signal.aborted) return;
      if (result.draft_id !== target.draft_id || result.patient_id !== access.patientId || result.form_type !== target.form_type
        || result.version !== target.version || result.status !== 'abandoned') throw new Error('服务返回结果与本次草稿放弃操作不一致。');
      setTarget(null); resource.reload();
    } catch (failure) {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setWriteError(failure); resource.reload(); }
    } finally {
      if (request.current === controller) { request.current = null; busy.current = false; setSubmitting(false); }
    }
  }
  const drafts = canRead ? resource.data?.drafts : undefined;
  if (drafts?.some(draft => draft.patient_id !== access.patientId)) throw new Error('草稿返回了其他患儿的资料。');
  const visible = drafts?.filter(draft => (!requestedId || draft.draft_id === requestedId)
    && (category === '全部' || formLabel[draft.form_type] === category));
  const currentTarget = drafts?.find(draft => draft.draft_id === target?.draft_id);
  const canRetry = !!currentTarget && currentTarget.version === target?.version && currentTarget.status === 'editing' && !resource.loading && !resource.error;
  return <UI2FamilyFrame title="我的草稿" screenId="UI2-21" note="继续编辑已保存的本人草稿。" backTo="/records"
    overlay={canRead && target && <UI2Sheet title="放弃草稿" onClose={close}>
      <p>确认放弃后，这份草稿将无法继续编辑。</p>
      <div className="pair"><span>草稿类别</span><strong>{formLabel[target.form_type]}</strong></div>
      <div className="pair"><span>保存版本</span><strong>{target.version}</strong></div>
      {writeError && <section className="status-panel error" role="alert"><h2>放弃结果需要核验</h2><p>{writeError.message}</p><p>重新读取当前保存状态后，才能确定是否可以再次提交。</p></section>}
      {writeError && <UI2ReadState requirePatient loading={resource.loading} error={resource.error} onRetry={resource.reload} />}
      {writeError && resource.data && !currentTarget && <p role="status">服务当前没有返回这份可恢复草稿。本次放弃结果尚未确认。</p>}
      {writeError && currentTarget && currentTarget.version !== target.version && <p role="status">草稿版本已经改变，请关闭弹层并重新选择当前版本。</p>}
      <button className="button" disabled={submitting || (writeError !== null && !canRetry)} onClick={() => void confirmAbandon()}>{submitting ? '正在提交放弃操作' : '确认放弃'}</button>
      <button className="button secondary" disabled={submitting} onClick={close}>{writeError ? '关闭并查看当前草稿' : '保留草稿'}</button>
    </UI2Sheet>}>
    {canRead && <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{active?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>}
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    {drafts && requestedId && !drafts.some(draft => draft.draft_id === requestedId) && <section className="status-panel stale" role="status">
      <h2>当前目标草稿无法恢复</h2><p>当前权限和可恢复目录没有返回这份准确草稿。</p>
      <ContextLink className="text-action" to="/drafts">查看全部获准草稿</ContextLink></section>}
    <div className="section-heading"><h2>继续未完成的记录</h2></div>
    <div className="segments" role="tablist" aria-label="草稿类别">{categories.map(value => <button key={value} role="tab" aria-selected={category === value} className={category === value ? 'selected' : ''} onClick={() => setCategory(value)}>{value}</button>)}</div>
    {canRead && visible && (visible.length === 0 ? <section className="card"><div className="empty"><span className="empty-icon"><FileClock size={54} /></span>
      <h3>{category === '全部' ? '没有可恢复的草稿' : '当前类别没有可恢复的草稿'}</h3><p>已确认保存的本人草稿会显示在这里。</p>
      <button className="text-action" onClick={() => navigate('/register/observations')}>添加记录<ArrowRight size={16} /></button>
    </div></section> : visible.map(draft => <section className="card form-card" key={draft.draft_id}>
      <div className="section-heading"><h2>{formLabel[draft.form_type]}草稿</h2><span className="badge neutral">已保存</span></div>
      <p>{active?.display_id}</p><div className="pair"><span>保存时间</span><strong>{eventTimeLabel(draft.saved_at)}</strong></div>
      <div className="pair"><span>保存版本</span><strong>{draft.version}</strong></div>
      <ContextLink className="button" to={draftPath(draft)}>继续编辑</ContextLink>
      <button className="text-action" onClick={() => { setTarget(draft); setWriteError(null); }}>放弃草稿</button>
    </section>))}
    <div className="note blue"><ShieldCheck size={17} /><p>没有新增资料，不代表当前健康状况正常。</p></div>
    <details className="card form-card"><summary>恢复草稿时需要注意什么</summary>
      <p>选择草稿后，系统检查本人身份、患儿权限、保存状态和记录版本。需要补充或重新确认的内容会在对应位置提示。</p>
      <p>已提交、已放弃或到期的草稿不能恢复。草稿只用于继续编辑，不进入时间轴、摘要或临床计算；尚未保存的内容在关闭或刷新后可能丢失。</p>
    </details>
  </UI2FamilyFrame>;
}
