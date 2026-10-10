import { useEffect, useRef, useState } from 'react';
import type { DatasetCounts, DatasetView, EvaluationDenominatorsView, EvaluationResultsView, EvaluationRuleProof, EvaluationRunView } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { readEvaluationDenominators, readEvaluationResults, readEvaluationRun } from '../../api/evaluationRuns';

const statuses: Record<EvaluationRunView['status'], string> = { accepted: '评价任务已受理', running: '正在评价固定资料',
  completed: '评价结果已保存', failed: '评价执行未完成', insufficient: '评价资料不足', superseded: '评价依据已变化' };
const unitLabels: Record<keyof DatasetCounts, string> = { files: '文件', pages: '页面', documents: '文书', patients: '独立患儿',
  encounters: '就诊', sampling_events: '采样事件', mas_events: 'MAS 事件', observation_periods: '观察区间',
  observation_person_hours: '观察人时', confirmed_labels: '已确认标签', unknown_labels: '未知标签' };
const resultLabels = { ...unitLabels, labels: '参考标签' };

export function EvaluationRunPanel({ dataset, runId, onPermission, onRules }: {
  dataset: DatasetView; runId: string | null; onPermission: () => void; onRules: (rules: EvaluationRuleProof[]) => void;
}) {
  const [run, setRun] = useState<EvaluationRunView | null>(null);
  const [denominators, setDenominators] = useState<EvaluationDenominatorsView | null>(null);
  const [results, setResults] = useState<EvaluationResultsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const latest = useRef<bigint | null>(null);
  useEffect(() => { latest.current = null; }, [dataset.version_id, runId]);
  useEffect(() => {
    setRun(null); setDenominators(null); setResults(null); setError(null); onRules([]);
    if (!runId || document.visibilityState !== 'visible') return;
    const controller = new AbortController(), started = performance.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function query() {
      try {
        const current = await readEvaluationRun(runId!, controller.signal);
        if (controller.signal.aborted) return;
        if (current.dataset_id !== dataset.dataset_id || current.dataset_version !== dataset.version)
          throw new Error('当前运行与页面所选资料版本不一致，请使用运行所对应的准确资料链接。');
        const version = BigInt(current.task.status_version);
        if (latest.current !== null && version < latest.current) throw new Error('评价任务状态版本已经变化，请重新读取。');
        latest.current = version;
        const counts = await readEvaluationDenominators(current.run_id, controller.signal);
        let saved: EvaluationResultsView | null = null;
        if (current.status === 'completed' || current.status === 'insufficient') {
          saved = await readEvaluationResults(current, controller.signal);
          if (saved.input_sha256 !== counts.input_sha256) throw new Error('评价结果与完整分母使用的输入版本不一致。');
        }
        if (controller.signal.aborted || document.visibilityState !== 'visible') return;
        setRun(current); setDenominators(counts); setResults(saved); onRules(saved?.rules ?? []);
        if (['completed', 'failed', 'insufficient', 'superseded'].includes(current.status)) return;
        const interval = current.task.poll_after_ms;
        if (interval === null || !Number.isSafeInteger(interval) || interval <= 0) throw new Error('评价任务查询间隔无效。');
        if (performance.now() - started + interval > 300000) { setError('页面自动查询已经结束，可主动读取当前评价任务。'); return; }
        timer = setTimeout(() => void query(), interval);
      } catch (failure) {
        if (controller.signal.aborted) return;
        setRun(null); setDenominators(null); setResults(null); onRules([]);
        setError(failure instanceof Error ? failure.message : '评价运行读取失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) onPermission();
      }
    }
    void query();
    return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); onRules([]); };
  }, [dataset.dataset_id, dataset.version, dataset.version_id, runId, refresh, onPermission, onRules]);
  if (!runId) return <section className="card form-card"><p>当前集合尚未选择固定版本的评价运行。</p></section>;
  return <section className={`status-panel ${error || run?.status === 'failed' ? 'error' : run?.status === 'insufficient' ? 'partial' : run?.status === 'completed' ? 'success' : 'pending'}`} role="status" style={{ overflowWrap: 'anywhere' }}>
    <h2>{error ? '暂时无法取得评价运行' : run ? statuses[run.status] : '正在读取评价运行'}</h2>
    {error && <p>{error}</p>}
    {run && <><p>资料版本 {run.dataset_version}；独立答案版本 {run.label_version_ref}</p>
      <p>历史回放结果保留在受控工作空间，不产生家庭通知。</p></>}
    {denominators && <>
      {Object.entries(denominators.denominators.counts).map(([unit, value]) => <div className="pair" key={unit}>
        <span>{unitLabels[unit as keyof DatasetCounts]}</span><strong>{value === null ? '未统计' : String(value)}</strong>
      </div>)}
      <p>未知标签 {denominators.denominators.excluded_unknown_label_ids.length} 项，单独保留。</p>
    </>}
    {results && <><p>已保存统计单位核验结果 {results.results.length} 项。</p>
      {results.results.map(item => <details key={item.unit} className="card form-card">
        <summary>{resultLabels[item.unit]}：{item.registered_items === null ? '未登记' : `${item.registered_items} 项`}</summary>
        <p>准确版本：{item.dataset_version_id}</p><p>结果引用：{item.result_ref}</p>
        {item.unit_ids.map(id => <p key={id}>对象身份：{id}</p>)}
        {item.evidence_refs.map(ref => <p key={ref}>证据引用：{ref}</p>)}
        {item.limitations.map(text => <p key={text}>{text}</p>)}
      </details>)}
      {results.limitations.some(item => item.reason === 'clinical_metrics_not_run') && <p>临床指标尚未测量，当前结果仅覆盖统计单位核验。</p>}
      {results.rules.map(rule => <div className="pair" key={rule.id}><span>{rule.config_key}</span><strong>版本 {rule.version}</strong></div>)}
    </>}
    {(error || run?.status === 'failed') && <button className="text-action" onClick={() => setRefresh(value => value + 1)}>重新读取评价运行</button>}
  </section>;
}
