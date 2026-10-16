import type { ClinicalMetricsReport, ClinicalMetricsOfflineReport, ClinicalMetricSummary,
  ClinicalIntervalResult, ClinicalIntervalSection } from '../../../../frontend-design/v2-types';

function shown(value: string | number | null) { return value === null ? '无法计算（分母为空）' : String(value); }
function numeric(value: number | null) { return value !== null && Number.isFinite(value) ? String(value) : '不可用'; }
const statusLabels: Record<string, string> = {
  COMPLETE: '全部区间已计算', INCOMPLETE: '区间计算未完整', PENDING: '等待计算', NOT_CONFIGURED: '未配置统计方案',
  UNSUPPORTED_PROTOCOL: '统计方案版本或计算环境不受支持', UNSUPPORTED_METHOD: '统计方法不受支持', RESOURCE_LIMIT: '达到计算资源上限',
  CALCULATED: '已计算', ZERO_DENOMINATOR: '实际分母为零', INSUFFICIENT_CLUSTERS: '患者簇或有贡献患者簇不足',
  INVALID_RESAMPLE: '存在无效重采样，未丢弃后继续计算', DEGENERATE_DISTRIBUTION: '重采样分布退化', NUMERIC_FAILURE: '数值计算失败',
};
const metricLabels: Record<ClinicalIntervalResult['metric_id'], string> = {
  sensitivity: '敏感度', missed_event_fraction: '漏检事件比例', ppv: '阳性预测值',
  alerts_per_observation_person_hour: '每观察人时提示数', alerts_per_negative_observation_person_hour: '每阴性观察人时提示数',
  event_unassessable_fraction: '无法评估阳性事件比例', first_alert_difference_mean_seconds: '已检出事件首次提示时间差均值',
  first_alert_difference_median_seconds: '已检出事件首次提示时间差中位数',
};
const unitLabels = { fraction: '比例', prompts_per_person_hour: '提示数 / 人时', seconds: '秒' };
const denominatorLabels: Record<ClinicalIntervalResult['denominator_policy'], string> = {
  assessable_independently_positive_events: '可评估的独立阳性事件数',
  known_outcome_prompts_including_first_and_repeat: '已知结局提示数（含首次与重复）',
  completed_period_hours_including_unknown_labels: '已完成区间观察人时（含未知标签）',
  completed_negative_period_hours: '已完成阴性区间观察人时',
  all_independently_positive_events: '全部独立阳性事件数', detected_events_only: '仅已检出事件数',
};
const aggregationLabels = { ratio_of_sums: '分子之和 / 分母之和', one_minus_ratio_of_sums: '1 − 检出数之和 / 分母之和',
  pooled_detected_event_mean: '合并已检出事件均值', pooled_detected_event_median: '合并已检出事件中位数' };
function statusText(value: string) { return `${statusLabels[value] ?? '未识别状态'}（${value}）`; }
function methodText(value: string) { return value === '未记录' ? '未记录' : value === 'patient_cluster_percentile_v1'
  ? '患者整簇百分位重采样（patient_cluster_percentile_v1）' : `未支持的方法（${value}）`; }
function reasonText(reasons: string[]) { return reasons.length ? reasons.map(statusText).join('；') : '无'; }
function rawChoice(protocol: unknown, key: string): string {
  if (protocol === null || typeof protocol !== 'object' || Array.isArray(protocol)) return '未记录';
  const value = (protocol as Record<string, unknown>)[key];
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '未记录';
}

function Metrics({ title, value, intervalSection }: { title: string; value: ClinicalMetricSummary; intervalSection: boolean }) {
  return <section aria-label={title} className="card form-card">
    <h3>{title}</h3>
    <dl>
      <dt>敏感度</dt><dd>{shown(value.sensitivity)}（{value.detected_events}/{value.assessable_positive_events} 个可评估阳性事件）</dd>
      <dt>阳性预测值</dt><dd>{shown(value.ppv)}（{value.positive_prompts}/{value.known_outcome_prompts} 个已知结局提示）</dd>
      <dt>首次 / 重复提示</dt><dd>{value.prompt_counts.first} / {value.prompt_counts.repeat}</dd>
      <dt>可评估 / 全部观察人时</dt><dd>{value.assessable_observation_person_hours} / {value.all_observation_person_hours}</dd>
      <dt>每观察人时提示数</dt><dd>{shown(value.alerts_per_observation_person_hour)}</dd>
      <dt>每阴性观察人时提示数</dt><dd>{shown(value.alerts_per_negative_observation_person_hour)}；阴性人时 {value.negative_observation_person_hours}</dd>
      <dt>无法评估阳性事件</dt><dd>{value.unassessable_positive_event_ids.length}</dd>
      <dt>未知提示结局</dt><dd>{value.unknown_outcome_prompts}</dd>
    </dl>
    {Object.entries(value.first_alert_difference_seconds_by_event).map(([id, seconds]) =>
      <p key={id}>事件 {id}：首次提示相对独立识别时间 {seconds} 秒（负值表示更早）。</p>)}
    {!intervalSection && <p>置信区间未计算。需要独立批准的患者相关性统计方案。</p>}
  </section>;
}

function IntervalResult({ value, available }: { value: ClinicalIntervalResult; available: boolean }) {
  // Schema rejects inconsistent API values; this defensive check also protects offline presentation.
  const calculated = available && value.status === 'CALCULATED' && value.estimate !== null && Number.isFinite(value.estimate)
    && value.lower !== null && Number.isFinite(value.lower) && value.upper !== null && Number.isFinite(value.upper)
    && value.lower <= value.upper && value.denominator !== null && Number.isFinite(value.denominator) && value.denominator > 0
    && value.numerator !== null && Number.isFinite(value.numerator) && Number.isFinite(value.confidence_level)
    && value.confidence_level > 0 && value.confidence_level < 1 && value.draws_valid >= 2 && value.draws_invalid === 0;
  return <section className="card form-card" aria-label={`区间 ${value.spec_id}`}>
    <h4>{metricLabels[value.metric_id]} · {value.mode === 'static' ? '静态路径' : '动态路径'}</h4>
    <p>状态：{calculated ? statusText(value.status) : value.status === 'CALCULATED' ? '区间值无效，未展示' : statusText(value.status)}；{value.required ? '必需项' : '可选项'}。</p>
    <dl>
      <dt>范围</dt><dd>{value.scope.partition === 'development' ? '开发集合' : '独立验证集合'}；{value.scope.subgroup
        ? `亚组 ${value.scope.subgroup.dimension} / ${value.scope.subgroup.value === 'unknown' ? '未知' : value.scope.subgroup.value}` : '整个分区'}（不合并分区）</dd>
      <dt>方法 / 置信水平</dt><dd>{methodText(value.method)}；{value.confidence_level * 100}% 双侧逐项区间</dd>
      <dt>汇总方式 / 单位</dt><dd>{aggregationLabels[value.aggregation]}；{unitLabels[value.unit]}</dd>
      <dt>点估计</dt><dd>{numeric(value.estimate)}</dd>
      <dt>置信区间</dt><dd data-clinical-interval={calculated ? 'calculated' : 'unavailable'}>{calculated
        ? `[${numeric(value.lower)}, ${numeric(value.upper)}] ${unitLabels[value.unit]}` : '未计算出有效区间'}</dd>
      <dt>实际分子 / 分母</dt><dd>{numeric(value.numerator)} / {numeric(value.denominator)}；分母口径：{denominatorLabels[value.denominator_policy]}</dd>
      <dt>患者簇 / 有贡献患者簇</dt><dd>{value.clusters} / {value.contributing_clusters}</dd>
      <dt>重采样请求 / 有效 / 无效次数</dt><dd>{value.draws_requested} / {value.draws_valid} / {value.draws_invalid}</dd>
      <dt>原因</dt><dd>{reasonText(value.reason_codes)}</dd>
      <dt>事件诊断计数</dt><dd>已检出 {value.diagnostics.detected_events}；漏检 {value.diagnostics.missed_events}；未知 {value.diagnostics.unknown_events}；无法评估阳性 {value.diagnostics.unassessable_positive_events}</dd>
      <dt>重采样序列摘要</dt><dd>{value.draw_sequence_sha256 ?? '未生成'}</dd>
    </dl>
  </section>;
}

function Intervals({ value }: { value: ClinicalIntervalSection }) {
  return <section aria-label="显式统计方案置信区间" style={{ overflowWrap: 'anywhere' }}>
    <h3>显式统计方案置信区间</h3>
    <p>区间整体状态：{statusText(value.status)}。必需项{value.required_complete ? '全部完成' : '尚未全部完成'}。</p>
    {value.status !== 'COMPLETE' && <p role="status">区间结果未完整。执行任务完成不代表统计区间全部完成；请核对各项状态与原因。</p>}
    <p>原因：{reasonText(value.reason_codes)}。</p>
    <p>未列出的指标：未请求（{value.unlisted_metrics_status}）；不补算或推定区间。</p>
    <p>当前实现仅支持明确方案指定的双侧逐项区间，无同时覆盖保证、无多重性调整；计算成功不表示统计方法或临床有效性已获批准。</p>
    <p>已检出事件时间差以“已检出”为条件，不是就医提前量。</p>
    <dl>
      <dt>统计方案</dt><dd>{value.protocol_ref ?? '未配置'}；版本 {rawChoice(value.protocol, 'schema_version')}</dd>
      <dt>方案方法</dt><dd>{methodText(rawChoice(value.protocol, 'method'))}</dd>
      <dt>方案摘要</dt><dd>{value.protocol_sha256 ?? '未提供'}</dd>
      <dt>输入摘要</dt><dd>{value.input_sha256}</dd>
      <dt>计算引擎</dt><dd>{methodText(value.engine.method)}；算法 {value.engine.algorithm_version}；NumPy {value.engine.numpy_version}；随机数生成器 {value.engine.rng_algorithm}</dd>
      <dt>实现摘要</dt><dd>{value.engine.implementation_sha256}</dd>
      <dt>工程资源上限</dt><dd>重采样 {value.engine.resource_limits.max_resamples} 次；患者簇 {value.engine.resource_limits.max_clusters}；抽样单元 {value.engine.resource_limits.max_draw_cells}；工作量 {value.engine.resource_limits.max_work}；时长 {value.engine.resource_limits.max_seconds} 秒（不是样本量建议）</dd>
    </dl>
    {value.results.length === 0 && <p>没有可展示的区间结果。</p>}
    {value.results.map(item => <IntervalResult key={item.spec_id} value={item} available={value.status === 'COMPLETE' || value.status === 'INCOMPLETE'} />)}
    <details><summary>完整冻结统计方案 / 原始不支持方案</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.protocol, null, 2)}</pre></details>
  </section>;
}

export function ClinicalMetricsPanel({ report }: { report: ClinicalMetricsReport | ClinicalMetricsOfflineReport }) {
  const verified = report.status === 'CALCULATED_FROM_VERIFIED_REPLAYS' && report.evidence_qualification === 'QUALIFIED_SAVED_REPLAY_BINDINGS';
  const title = verified ? '已核验历史回放指标' : '声明资料离线计算指标';
  const intervals = report.schema_version === 'clinical-metrics-report-3' ? report.confidence_intervals : null;
  return <section aria-label={title}>
    <h2>{title}</h2>
    <p>{report.data_mode === 'synthetic' ? '合成软件测试资料' : '受控评价资料'}。{verified
      ? '已核对保存的回放、来源和固定方案；不表示临床有效性已获批准。'
      : '仅基于声明资料计算，未独立核验来源证据、批准或保存的回放任务；不表示临床有效性已获批准。'}</p>
    <p>方案 {report.binding.measurement_plan_ref}；固定版本 {report.binding.dataset_version_id}。</p>
    {report.sequence_policy && <>
      <p>每个独立预定时点使用自己的可得资料。仅全部时点完成的区间进入可评估分母。</p>
      <p>方案中的计数规则：区间内第一个阳性时点计为首次，其后阳性时点计为重复；仅在区间边界重置。此处不运行当前提醒周期。</p>
    </>}
    <Metrics title="静态路径" value={report.static} intervalSection={!!intervals} /><Metrics title="动态路径" value={report.dynamic} intervalSection={!!intervals} />
    {(['development', 'validation'] as const).map(partition => <details key={partition}>
      <summary>{partition === 'development' ? '开发集合' : '独立验证集合'}：{report.partitions[partition].patients} 人 / {report.partitions[partition].periods} 区间</summary>
      <Metrics title="静态路径" value={report.partitions[partition].static} intervalSection={!!intervals} />
      <Metrics title="动态路径" value={report.partitions[partition].dynamic} intervalSection={!!intervals} />
    </details>)}
    {Object.entries(report.subgroups).map(([dimension, groups]) => <details key={dimension}>
      <summary>亚组 {dimension}</summary>
      {Object.entries(groups).map(([name, group]) => <section key={name}>
        <h3>{name === 'unknown' ? '未知' : name}：{group.patients} 人</h3>
        <Metrics title="静态路径" value={group.static} intervalSection={!!intervals} /><Metrics title="动态路径" value={group.dynamic} intervalSection={!!intervals} />
      </section>)}
    </details>)}
    {intervals && <Intervals value={intervals} />}
    <details><summary>准确执行与局限</summary>
      {report.execution_steps?.map(step => <p key={`${step.period_id}:${step.step_id}:${step.mode}`}>
        {step.step_id} / {step.mode}：{step.status}；任务 {step.task_id}；资料摘要 {step.corpus_sha256}；执行摘要 {step.execution_input_sha256}。
      </p>)}
      {report.limitations.map(value => <p key={value}>{value}</p>)}
    </details>
  </section>;
}
