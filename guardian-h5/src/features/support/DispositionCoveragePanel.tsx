import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { readDispositionCoverage, type DispositionCoverage } from '../../api/dispositionCoverage';

export function DispositionCoveragePanel({ active, onPermission }: { active: boolean; onPermission: () => void }) {
  const [patient, setPatient] = useState(''), [purpose, setPurpose] = useState('');
  const [value, setValue] = useState<DispositionCoverage | null>(null), [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const generation = useRef(0);
  function clear(clearInputs = false) {
    generation.current += 1; pending.current?.abort(); pending.current = null;
    setValue(null); setError(null); setBusy(false);
    if (clearInputs) { setPatient(''); setPurpose(''); }
  }
  useEffect(() => {
    if (!active) clear(true);
    const hidden = () => { if (document.visibilityState !== 'visible') clear(true); };
    const leaving = () => clear(true);
    document.addEventListener('visibilitychange', hidden); window.addEventListener('pagehide', leaving);
    return () => { generation.current += 1; pending.current?.abort(); document.removeEventListener('visibilitychange', hidden); window.removeEventListener('pagehide', leaving); };
  }, [active]);
  async function query() {
    if (!active || busy) return;
    clear(); const current = generation.current;
    const controller = new AbortController(); pending.current = controller; setBusy(true);
    try {
      const result = await readDispositionCoverage(patient.trim(), purpose.trim(), controller.signal);
      if (!controller.signal.aborted && current === generation.current) setValue(result);
    } catch (failure) {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted && current === generation.current) {
        setValue(null); setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setPatient(''); setPurpose(''); onPermission(); }
      }
    } finally { if (current === generation.current) { setBusy(false); pending.current = null; } }
  }
  if (!active) return null;
  return <section className="card form-card" style={{ overflowWrap: 'anywhere' }}>
    <h2>处置覆盖与依赖清单</h2>
    <p role="note">只读查询，需要当前运维岗位和该用途下的完整患儿范围。清单不会删除资料、批准处置或扩大原申请。数据库类别和备份的处置执行器尚未提供。</p>
    <label className="field"><span>患儿 UUID</span><input aria-label="覆盖查询患儿 UUID" value={patient} onChange={event => { clear(); setPatient(event.target.value); }} /></label>
    <label className="field"><span>已授权用途</span><input aria-label="覆盖查询已授权用途" value={purpose} onChange={event => { clear(); setPurpose(event.target.value); }} /></label>
    <button className="button secondary" disabled={busy || !patient.trim() || !purpose.trim()} onClick={() => void query()}>查询只读覆盖清单</button>
    {busy && <p role="status">正在核查元数据权限和覆盖范围</p>}{error && <p role="alert">{error.message}</p>}
    {value && <>
      <p>本次患儿 {value.patient_id}；查询时间 {value.observed_at}。</p>
      <p>清单指纹 {value.manifest_sha256}</p>
      {(value.inventory_truncated || value.dependencies.truncated) && <p role="status">资料超过本次有界清单容量，以下仅为部分结果，不能证明完整覆盖。</p>}
      {value.dependencies.cross_patient_edge_rejected && <p role="alert">检测到跨患儿依赖异常，相关标识未展示，需核查来源。</p>}
      <h3>现有执行能力</h3>{value.catalog.executors.map(item => <p key={item.target_type}>{item.target_type}：{item.availability}，范围 {item.coverage}{item.environment_supported === false ? '；当前环境不支持' : ''}</p>)}
      <h3>尚无执行器的类别</h3>{value.catalog.unsupported_categories.map(item => <p key={item.category}>{item.category}：{item.required_contract}</p>)}
      <h3>当前数据库对象元数据</h3>{value.inventory.filter(item => item.total_objects > 0).map(item => <p key={item.model}>{item.model}：共 {item.total_objects} 项，本次列出 {item.included_objects} 项</p>)}
      <details><summary>查看准确对象、版本与引用</summary>{value.objects.map(item => <article key={`${item.model}:${item.id}`}><p>{item.model} · {item.id}</p><p>版本 {Object.entries(item.versions).map(([key, val]) => `${key}=${val}`).join('；') || '仅有对象身份，尚无独立处置版本契约'}</p><p>保留策略 {item.retention_policy_id ?? '未直接绑定；需核查继承规则'}</p>{item.references.map(ref => <p key={ref.field}>{ref.field} → {ref.id}</p>)}</article>)}</details>
      <h3>已配置保留策略</h3>{value.policies.map(item => <p key={item.policy_id}>{item.policy_id} · v{item.version}；批准元数据{item.approval_metadata_current ? '当前有效' : '不完整或不可用'}；配置处置 {item.configured_disposition ?? '无效'}；保留天数 {item.configured_retention_days ?? '未配置有限期限'}。未核验批准证据内容、对象起算时间或法律临床保留条件。</p>)}
      <details><summary>查看已登记依赖边（并非完整删除范围）</summary>{value.dependencies.edges.map(edge => <p key={edge.id}>{edge.dependent_node_id} → {edge.prerequisite.resource_type} · {edge.prerequisite.resource_id} · {edge.prerequisite.resource_version} ({edge.edge_type})</p>)}</details>
      <h3>执行前仍需具备</h3>{value.catalog.execution_prerequisites.map(item => <p key={item}>{item}</p>)}
      <details><summary>未覆盖的存储与引用范围</summary>{value.unresolved_coverage.map(item => <p key={item}>{item}</p>)}</details>
    </>}
  </section>;
}
