import { useNavigate } from 'react-router';
import type { DynamicView, DynamicWeightFraction, DynamicWeightedResult } from '../../../../frontend-design/v2-types';

const states = { met: '满足', not_met: '未满足', unknown: '未知' };
const roles = { baseline: '基准采样', current: '当前采样', series: '连续采样', context: '专业背景' };
const actions = { create_event: '已经建立提示事件', continue_episode: '继续当前周期', close_episode: '满足批准恢复条件',
  no_event: '没有建立新事件', blocked: '发布条件尚未满足' };

export function dynamicSummary(result: DynamicView | null): [string, string][] {
  const labels = ['基准与当前采样', '独立连续采样', '三值组合结果', '持续提示周期'];
  if (!result || result.validity !== 'valid' || !result.combinations || !result.lifecycle) return labels.map(label => [label, '尚无有效评估']);
  if (result.combinations.trigger.groups.some(group => group.operator === 'weighted')) labels[2] = '加权组合结果';
  const selected = Object.entries(result.selected_refs).filter(([, value]) => value !== null);
  return [[labels[0], `基准 ${selected.filter(([key]) => key.startsWith('baseline:')).length} 项；当前 ${selected.filter(([key]) => key.startsWith('current:')).length} 项`],
    [labels[1], `${result.signals.filter(signal => signal.signal_id.startsWith('trigger:')).reduce((sum, signal) => sum + signal.pair_results.length, 0)} 次完整相邻比较`],
    [labels[2], states[result.decision]], [labels[3], result.mode === 'replay' ? '历史回放独立保存'
      : result.status === 'superseded' ? '较早评估保留用于追溯' : actions[result.lifecycle.action]]];
}

function exactFraction(value: DynamicWeightFraction) {
  return `${value.numerator} / ${value.denominator}`;
}

export function WeightedGroupDetails({ value }: { value: DynamicWeightedResult }) {
  return <section aria-label="批准加权规则解释">
    <h4>核心与一般指标</h4>
    {value.members.map(member => <div className="pair" key={member.signal_ref}>
      <span>{member.signal_ref} · {member.classification === 'core' ? '核心指标' : '一般指标'}</span>
      <strong>权重 {member.weight} · {states[member.state]}</strong>
    </div>)}
    <div className="pair"><span>完整权重分母</span><strong>{value.denominator_weight}</strong></div>
    <div className="pair"><span>已满足／已知／未知权重</span><strong>{value.met_weight}／{value.known_weight}／{value.unknown_weight}</strong></div>
    <div className="pair"><span>精确加权下界</span><strong>{exactFraction(value.lower_fraction)}</strong></div>
    <div className="pair"><span>精确加权上界</span><strong>{exactFraction(value.upper_fraction)}</strong></div>
    <div className="pair"><span>批准阈值（比例）</span><strong>{value.threshold_fraction} · {states[value.threshold_state]}</strong></div>
    <div className="pair"><span>已知权重比例</span><strong>{exactFraction(value.known_fraction)}</strong></div>
    <div className="pair"><span>最低已知权重比例</span><strong>{value.minimum_known_fraction} · {states[value.coverage_state]}</strong></div>
    <div className="pair"><span>批准核心条件</span><strong>{value.core_gate.operator === 'all' ? '全部核心指标满足' : `至少 ${value.core_gate.minimum_met} 项核心指标满足`} · {states[value.core_state]}</strong></div>
    <p className="muted">未知指标保留在完整分母中，不补为正常或阴性。下界只计已满足权重，上界还包含未知权重。</p>
    <p className="muted">这是获批规则的计算结果，未满足不能排除风险，不能替代专业诊疗判断。</p>
    {value.reason_codes.length > 0 && <p role="status">{value.reason_codes.join('；')}</p>}
  </section>;
}

export function DynamicSources({ result }: { result: DynamicView }) {
  const navigate = useNavigate();
  if (result.validity !== 'valid' || !result.combinations || !result.lifecycle) return null;
  return <>
    <p>评估时点：{result.assessment_at}</p><p>资料截止时点：{result.known_at}</p>
    <p>规则：{result.policy_id}</p><p>窗口：{result.window_policy_id}</p>
    {result.sensitivity_preset && <section className="card form-card" aria-label="采用的批准方案">
      <h3>{result.sensitivity_preset.display_name}</h3><p>{result.sensitivity_preset.description}</p>
      <div className="pair"><span>方案标识</span><strong>{result.sensitivity_preset.preset_key}</strong></div>
      <div className="pair"><span>准确配置版本</span><strong>{result.sensitivity_preset.config_id}</strong></div>
      <div className="pair"><span>配置校验摘要</span><strong>{result.sensitivity_preset.config_checksum}</strong></div>
    </section>}
    {result.automatic_trigger && <section className="card form-card"><h3>实际自动触发</h3>
      <p>处理任务：{result.automatic_trigger.trigger_task_id}</p><p>批准配置：{result.automatic_trigger.schedule.id}</p>
      <p>来源事件：{result.automatic_trigger.source.id}</p></section>}
    {Object.entries(result.selected_refs).map(([key, reference]) => <section className="card form-card" key={key}>
      <h3>{key}</h3><p>{reference ? `采用事实版本：${String(reference.fact_version_id)}` : '尚无合格输入'}</p>
    </section>)}
    {result.signals.map(signal => <section className="card form-card" key={signal.signal_id}>
      <h3>{signal.signal_id}：{states[signal.state]}</h3><p>{signal.reason_codes.join('；')}</p>
      {signal.assessment_id && <button className="text-action" onClick={() => navigate(`/support/assessment?${new URLSearchParams({ assessment_id: signal.assessment_id!, patient_id: result.patient_id,
        evaluation_kind: signal.assessment_kind! })}`)}>读取准确静态评估</button>}
      {signal.inputs.map(input => <section key={input.position}>
        <h4>{input.variable_code} · {roles[input.input_role]}</h4><p>采用值：{input.value} {String(input.reference.normalized_unit ?? '')}</p>
        {['fact_version_id', 'record_version_id', 'field_id', 'verification_id', 'normalization_id', 'medical_time_id', 'source_revision_id', 'document_version_id', 'page_id']
          .map(key => <div className="pair" key={key}><span>{key}</span><strong>{input.reference[key] === null ? '未使用' : String(input.reference[key])}</strong></div>)}
        <button className="text-action" onClick={() => navigate(`/support/verification?${new URLSearchParams({ verification_id: String(input.reference.verification_id) })}`)}>读取核对证明与原件</button>
      </section>)}
      {[...signal.pair_results, ...(signal.baseline_current_result ? [signal.baseline_current_result] : [])].map((pair, index) => <section key={index}>
        <h4>{index < signal.pair_results.length ? `相邻比较 ${pair.position + 1}` : '基准与当前比较'}：{states[pair.state]}</h4>
        <p>基准：{pair.baseline_field_id} · {pair.baseline_time.earliest} 至 {pair.baseline_time.latest_exclusive ?? pair.baseline_time.instant}</p>
        <p>当前：{pair.current_field_id} · {pair.current_time.earliest} 至 {pair.current_time.latest_exclusive ?? pair.current_time.instant}</p>
        <div className="pair"><span>绝对变化</span><strong>{pair.absolute_delta ?? '尚不能计算'}</strong></div>
        <div className="pair"><span>相对变化</span><strong>{pair.relative_delta === null ? '尚不能计算' : `${pair.relative_delta}%`}</strong></div>
        <p>{[...pair.reason_codes, ...(pair.relative_reason ? [pair.relative_reason] : [])].join('；')}</p>
      </section>)}
    </section>)}
    {Object.entries(result.combinations).map(([name, value]) => <section className="card form-card" key={name}>
      <h3>{name === 'trigger' ? '提示条件' : name === 'reset' ? '恢复条件' : '再次提醒条件'}：{states[value.state]}</h3>
      {value.groups.map(group => <section key={group.path.join(':')}>
        <p>{group.path.length ? group.path.join('／') : '整体条件'} · {group.operator === 'weighted' ? '批准加权组合' : group.operator} · {states[group.state]} · {group.operands.map(state => states[state]).join('；')}</p>
        {group.operator === 'weighted' && <WeightedGroupDetails value={group.weighted} />}
      </section>)}
    </section>)}
    {result.clinical_selections.map(item => <section className="card form-card" key={`${item.input_role}:${item.choice.variable_code}`}>
      <h3>{item.choice.variable_code} · {roles[item.input_role]}</h3><p>{item.choice.reason}</p><p>专业人员：{item.actor_id}</p>
      <p>原始依据：{item.choice.evidence_ref}</p><p>保存时间：{item.saved_at}</p>
      {item.choice.prior_task_id && <p>原选择任务：{item.choice.prior_task_id}</p>}
    </section>)}
    {result.exclusions.map((item, index) => <p key={index}>未采用事实：{String(item.fact_version_id)}；{Array.isArray(item.reason_codes) ? item.reason_codes.join('；') : ''}</p>)}
    {result.relation_proofs.map(item => <section className="card form-card" key={item.relation_id}>
      <h3>采样关系：{item.relation_id}</h3><p>{item.reason_codes.join('；')}</p>
      {item.references.map(reference => <div className="pair" key={`${reference.model}:${reference.id}`}><span>{reference.model}</span><strong>{reference.id}</strong></div>)}
    </section>)}
    <p>当前周期：{result.lifecycle.episode_id ?? '尚未建立'}</p><p>{actions[result.lifecycle.action]}</p>
    <p>{result.lifecycle.reason_codes.join('；')}</p>
  </>;
}
