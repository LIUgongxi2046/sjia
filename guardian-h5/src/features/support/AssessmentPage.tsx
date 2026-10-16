import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ChevronRight, LockKeyhole, ShieldCheck } from 'lucide-react';
import type { AssessmentView, DynamicView, SupportSessionView, TaskView } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { readAssessment, readAssessmentTask } from '../../api/assessments';
import { UI2Frame, UI2Sheet } from '../../components/UI2Frame';
import { FileText } from '../../components/ThreeDIcon';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';
import { useAssessmentCreation } from './useAssessmentCreation';
import { readDynamic, readDynamicTask } from '../../api/dynamic';
import { DynamicSources, dynamicSummary } from './DynamicSources';
import { DynamicTriggerRecords } from './DynamicTriggerRecords';
import { HlhReferenceResult } from './HlhReferenceResult';

const tabs = ['静态分类', 'MS 评分', 'HLH-2004', '动态变化'];
const variableNames: Record<string, string> = { sjia: 'sJIA 状态', fever: '发热状态', ferritin: '铁蛋白',
  platelet_count: '血小板计数', ast: 'AST', triglycerides: '甘油三酯', fibrinogen: '纤维蛋白原', ldh: 'LDH',
  applicability: 'HLH 适用依据', molecular: 'HLH 分子依据', splenomegaly: '脾大', hemoglobin: '血红蛋白',
  neutrophil_count: '中性粒细胞', triglycerides_fasting: '空腹依据', hemophagocytosis: '噬血现象',
  no_malignancy_evidence: '恶性肿瘤限定依据', nk_activity: 'NK 活性', soluble_cd25: '可溶性 CD25', age_at_hemoglobin: '采样年龄',
  cns: '中枢神经系统受累', hemorrhagic_manifestations: '出血表现', active_arthritis: '活动性关节炎' };
const conditionNames: Record<string, string> = { met: '满足', not_met: '未满足', unknown: '未知', known: '已确认',
  insufficient: '资料不足', meets: '符合分类条件', not_meets: '未达到分类条件', not_applicable: '不适用' };
const referenceNames: Record<string, string> = { record_version_id: '记录版本', field_id: '字段身份', verification_id: '核对证明',
  normalization_id: '标准化结果', medical_time_id: '医学时间', source_revision_id: '来源版本',
  document_version_id: '文书版本', page_id: '原件页面' };
function condition(value: string) {
  const label = conditionNames[value];
  if (!label) throw new Error('评估条件含有未登记状态。');
  return label;
}

function summary(result: AssessmentView | null, tab: string): [string, string][] {
  const labels = tab === '静态分类' ? ['适用前提', '铁蛋白必需条件', '四项检验条件', '整体判定']
    : tab === 'MS 评分' ? ['七项输入资格', '各项贡献', '完整总分', '阈值比较']
      : ['基准与当前采样', '独立连续采样', '三值组合结果', '持续提示周期'];
  if (!result || result.validity !== 'valid' || !result.output || tab === '动态变化'
      || (tab === '静态分类' ? result.kind !== 'classification' : result.kind !== 'ms')) return labels.map(label => [label, '尚无有效评估']);
  const output = result.output;
  if (result.kind === 'classification') {
    const values = output.classification_conditions!;
    return [[labels[0], `sJIA：${condition(values.sjia)}；发热：${condition(values.fever)}`],
      [labels[1], condition(values['BR-C01'])],
      [labels[2], [['BR-C02', '血小板'], ['BR-C03', 'AST'], ['BR-C04', '甘油三酯'], ['BR-C05', '纤维蛋白原']]
        .map(([code, name]) => `${name}：${condition(values[code])}`).join('；')],
      [labels[3], condition(output.classification_result!)]];
  }
  const required = ['cns', 'hemorrhagic_manifestations', 'active_arthritis', 'platelet_count', 'ldh', 'fibrinogen', 'ferritin'];
  const present = new Set(result.inputs.map(input => input.variable_code));
  const contributions = Object.entries(output.ms_contributions!);
  return [[labels[0], `${required.filter(code => present.has(code)).length}／7 项`],
    [labels[1], contributions.length ? contributions.map(([code, value]) => `${variableNames[code] ?? code}：${value}`).join('；') : '资料不足'],
    [labels[2], output.ms_total ?? '尚无完整总分'],
    [labels[3], output.ms_threshold_result === null ? '尚未完成比较' : output.ms_threshold_result ? '达到批准阈值' : '未达到批准阈值']];
}

export function AssessmentPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const [parameters] = useSearchParams();
  const assessmentId = parameters.get('assessment_id'), taskId = parameters.get('task_id');
  const evaluationId = parameters.get('evaluation_id'), evaluationKind = parameters.get('evaluation_kind');
  const dynamic = evaluationId !== null || evaluationKind === 'dynamic';
  const patientId = parameters.get('patient_id');
  const available = accessState === 'default' && session !== null && session.capabilities.some(item =>
    item.capability_id === 'read_assessment' && ['available', 'read_only'].includes(item.state));
  const [tab, setTab] = useState(dynamic ? tabs[3] : evaluationKind === 'hlh' ? tabs[2] : evaluationKind === 'ms' ? tabs[1] : tabs[0]);
  useEffect(() => {
    if (evaluationKind === 'dynamic' || evaluationId) setTab(tabs[3]);
    else if (evaluationKind === 'hlh') setTab(tabs[2]);
    else if (evaluationKind === 'ms') setTab(tabs[1]);
    else if (evaluationKind === 'classification') setTab(tabs[0]);
  }, [evaluationKind, evaluationId, taskId, assessmentId]);
  const creation = useAssessmentCreation({ session, available: accessState === 'default',
    kind: tab === '静态分类' ? 'classification' : tab === 'MS 评分' ? 'ms' : tab === 'HLH-2004' ? 'hlh' : 'dynamic', onRefresh });
  const [result, setResult] = useState<AssessmentView | null>(null);
  const [dynamicResult, setDynamicResult] = useState<DynamicView | null>(null);
  const [task, setTask] = useState<TaskView | null>(null);
  const [state, setState] = useState<SupportReadState>('default');
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [sources, setSources] = useState(false);
  const latest = useRef<bigint | null>(null);
  useEffect(() => { latest.current = null; }, [assessmentId, evaluationId, taskId, dynamic, session?.principal_id]);
  useEffect(() => {
    setResult(null); setDynamicResult(null); setTask(null); setSources(false); setError(null); setState('default');
    if (!available || (!assessmentId && !evaluationId && !taskId)) return;
    const controller = new AbortController(), started = performance.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function query() {
      setState('loading');
      try {
        let currentTask: TaskView | null = null, currentResult: AssessmentView | null = null, currentDynamic: DynamicView | null = null;
        if (taskId) {
          currentTask = dynamic ? await readDynamicTask(taskId, controller.signal) : await readAssessmentTask(taskId, controller.signal);
          const version = BigInt(currentTask.status_version);
          if (latest.current !== null && version < latest.current) throw new Error('任务状态版本已经变化，请重新读取。');
          latest.current = version;
          if (currentTask.status === 'completed') {
            if (assessmentId && currentTask.result_ref !== assessmentId) throw new Error('页面评估与任务结果不一致。');
            if (dynamic) {
              if (evaluationId && currentTask.result_ref !== evaluationId) throw new Error('页面动态评估与任务结果不一致。');
              currentDynamic = await readDynamic(currentTask.result_ref!, controller.signal);
            } else currentResult = await readAssessment(currentTask.result_ref!, controller.signal);
          }
        } else if (dynamic) currentDynamic = await readDynamic(evaluationId!, controller.signal);
        else currentResult = await readAssessment(assessmentId!, controller.signal);
        if (currentResult && (patientId && currentResult.patient_id !== patientId || taskId && currentResult.task.task_id !== taskId))
          throw new Error('当前评估结果与页面资料范围不一致。');
        if (currentDynamic && (patientId && currentDynamic.patient_id !== patientId || taskId && currentDynamic.task.task_id !== taskId))
          throw new Error('当前动态评估与页面资料范围不一致。');
        if (controller.signal.aborted || document.visibilityState !== 'visible') return;
        setTask(currentTask ?? currentResult?.task ?? currentDynamic?.task ?? null); setResult(currentResult); setDynamicResult(currentDynamic); setState('default');
        if (currentTask?.poll_after_ms !== null && currentTask?.poll_after_ms !== undefined) {
          const interval = currentTask.poll_after_ms;
          if (performance.now() - started + interval > 300000) {
            setError('页面自动查询已经结束，可主动读取当前评估任务。'); return;
          }
          timer = setTimeout(() => void query(), interval);
        }
      } catch (failure) {
        if (controller.signal.aborted) return;
        setResult(null); setDynamicResult(null); setTask(null); setSources(false);
        const denied = failure instanceof ApiError && [401, 403].includes(failure.status);
        setState(denied ? 'permission' : navigator.onLine ? 'error' : 'offline');
        setError(failure instanceof Error ? failure.message : '评估依据读取失败。');
        if (denied) onRefresh();
      }
    }
    if (document.visibilityState === 'visible') void query();
    function suspend() {
      if (document.visibilityState === 'visible') { setRefresh(value => value + 1); return; }
      controller.abort(); if (timer !== undefined) clearTimeout(timer);
      setResult(null); setDynamicResult(null); setTask(null); setSources(false); setState('loading');
    }
    const restore = () => setRefresh(value => value + 1);
    document.addEventListener('visibilitychange', suspend); window.addEventListener('pageshow', restore);
    return () => {
      controller.abort(); if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener('visibilitychange', suspend); window.removeEventListener('pageshow', restore);
    };
  }, [available, assessmentId, evaluationId, dynamic, taskId, patientId, session?.principal_id, refresh, onRefresh]);
  const visible = available && state === 'default';
  const matchingKind = tab === '静态分类' ? 'classification' : tab === 'MS 评分' ? 'ms' : tab === 'HLH-2004' ? 'hlh' : null;
  const displayed = visible && result?.kind === matchingKind ? result : null;
  const displayedDynamic = visible && tab === '动态变化' ? dynamicResult : null;
  const selectedResult = tab === '动态变化' ? displayedDynamic : displayed;
  const readState = accessState !== 'default' ? accessState : !available ? 'permission' : state;
  return <UI2Frame title="评估依据" screenId="UI2-35" note="静态分类、MS、HLH-2004 与动态路径分别保存结果。" onBack={() => navigate('/support/verification')}
    overlay={creation.overlay || displayedDynamic?.validity === 'valid' && sources && <UI2Sheet title="观察时段与来源" onClose={() => setSources(false)}>
      <DynamicSources result={displayedDynamic} />
      {session && <DynamicTriggerRecords patientId={displayedDynamic.patient_id} principalId={session.principal_id} onPermissionLost={onRefresh} />}
      {creation.canCreate && <button className="button" onClick={() => { setSources(false); void creation.openCreation(); }}>创建当前动态评估</button>}
      <button className="button secondary" onClick={() => setSources(false)}>继续查看</button>
    </UI2Sheet> || tab !== '动态变化' && visible && displayed?.validity === 'valid' && sources && <UI2Sheet title="观察时段与来源" onClose={() => setSources(false)}>
      <p>评估时点：{displayed.assessment_at}</p><p>资料截止时点：{displayed.known_at}</p>
      <p>规则：{displayed.rule_id}</p><p>窗口：{displayed.window_policy_id}</p>
      {displayed.inputs.map(input => <section className="card form-card" key={input.variable_code}>
        <h3>{variableNames[input.variable_code] ?? input.variable_code}</h3>
        <p>采用值：{input.input_snapshot.value}{input.input_snapshot.unit ? ` ${input.input_snapshot.unit}` : ''}</p>
        {['record_version_id', 'field_id', 'verification_id', 'normalization_id', 'medical_time_id', 'source_revision_id', 'document_version_id', 'page_id']
          .map(key => <div className="pair" key={key}><span>{referenceNames[key]}</span><strong>{input.input_snapshot.reference[key] === null ? '未使用' : String(input.input_snapshot.reference[key])}</strong></div>)}
      </section>)}
      {displayed.clinical_selections.map(item => <section className="card form-card" key={item.variable_code}>
        <h3>{variableNames[item.variable_code] ?? item.variable_code} · 专业选择</h3><p>{item.reason}</p>
        <div className="pair"><span>事实版本</span><strong>{item.fact_version_id}</strong></div>
        <div className="pair"><span>专业人员</span><strong>{item.actor_id}</strong></div>
        <div className="pair"><span>准确依据</span><strong>{item.evidence_ref}</strong></div>
        <div className="pair"><span>实际保存时间</span><strong>{item.saved_at}</strong></div>
        {item.prior_task_id && <div className="pair"><span>原选择任务</span><strong>{item.prior_task_id}</strong></div>}
      </section>)}
      {creation.canCreate && <button className="button" onClick={() => { setSources(false); void creation.openCreation(); }}>创建当前{tab === 'MS 评分' ? 'MS 评分' : tab === 'HLH-2004' ? 'HLH-2004 参考评估' : '静态分类'}</button>}
      <button className="button secondary" onClick={() => setSources(false)}>继续查看</button>
    </UI2Sheet>}>
    <div className="support-banner"><LockKeyhole size={16} />受控支持 · 需独立岗位授权</div>
    <SupportReadStatus state={readState} error={accessState !== 'default' ? accessError : error}
      onRefresh={accessState !== 'default' || !available || readState === 'permission' ? onRefresh : () => setRefresh(value => value + 1)} />
    <section className="card form-card"><div className="eyebrow">指定范围内操作</div><h2>评估依据</h2>
      <p className="muted">静态分类、MS、HLH-2004 与动态路径分别保存结果。</p>
      <div className="pair"><span>当前资料范围</span><strong>{selectedResult ? selectedResult.patient_id : '尚未授权选择'}</strong></div>
    </section>
    <div className="segments" role="tablist" aria-label="评估方式">{tabs.map(value => <button key={value} role="tab"
      aria-selected={tab === value} className={tab === value ? 'selected' : ''} onClick={() => { setSources(false); setTab(value); }}>{value}</button>)}</div>
    {visible && selectedResult?.validity === 'valid' && (tab === 'HLH-2004' ? <HlhReferenceResult result={displayed?.validity === 'valid' ? displayed.output?.hlh_result ?? null : null} />
      : <section className="card form-card">{(tab === '动态变化' ? dynamicSummary(displayedDynamic) : summary(displayed, tab)).map(([label, value]) =>
        <div className="pair" key={label}><span>{label}</span><strong>{value}</strong></div>)}</section>)}
    {visible && !selectedResult && <section className="card form-card"><p>{!assessmentId && !evaluationId && !taskId
      ? '请选择获准的评估结果，或按授权范围创建评估。' : task && !['completed', 'failed', 'cancelled', 'superseded'].includes(task.status)
        ? '评估任务尚未完成，当前还没有可显示的结果。' : '当前选择尚未取得可显示的评估结果，请核对结果类型与任务状态。'}</p></section>}
    {creation.content}
    <section className="card">
      <button className="list-row" onClick={() => {
        if (selectedResult?.validity === 'valid') setSources(true); else void creation.openCreation();
      }} disabled={selectedResult?.validity !== 'valid' && !creation.canCreate}>
        <span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>观察时段与来源</strong></span><ChevronRight size={17} />
      </button>
      <button className="list-row" onClick={() => navigate('/support/publication')}>
        <span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>内容发布条件</strong></span><ChevronRight size={17} />
      </button>
    </section>
    {visible && task && ['failed', 'cancelled', 'superseded'].includes(task.status) && <section className="status-panel error" role="status">
      <h2>当前评估任务没有可采用结果</h2><p>{task.error_code ?? '任务已终止。'}</p>
      <button className="text-action" onClick={() => setRefresh(value => value + 1)}>重新读取评估任务</button>
    </section>}
    {visible && error && <section className="status-panel" role="status"><p>{error}</p>
      <button className="text-action" onClick={() => setRefresh(value => value + 1)}>读取当前评估任务</button></section>}
    {selectedResult?.validity === 'invalid' && <section className="status-panel stale" role="status">
      <h2>原评估依据已更新</h2><p>需要按当前合格资料重新计算。</p></section>}
    {displayedDynamic?.status === 'superseded' && <section className="status-panel stale" role="status">
      <h2>已有较新的评估上下文</h2><p>当前结果保留用于追溯，家庭内容与周期使用最新采用依据。</p></section>}
    {selectedResult?.validity === 'valid' && (selectedResult.missing_items.length > 0 || selectedResult.limitations.length > 0) && <section className="status-panel partial" role="status">
      <h2>评估范围与资料不足</h2>{[...selectedResult.missing_items, ...selectedResult.limitations].map((reason, index) =>
        <p key={`${reason.variable_code}:${index}`}>{variableNames[reason.variable_code] ?? reason.variable_code}：{reason.reason_codes.join('；')}</p>)}
    </section>}
    <div className="note"><ShieldCheck size={17} /><p>历史回放不会发布到家庭或发送通知。</p></div>
  </UI2Frame>;
}
