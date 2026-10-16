import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import { History, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2Sheet } from '../../components/UI2Frame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useAnalyteResource } from '../../api/useAnalyteResource';
import { medicalSourceTimeLabel } from '../../components/recordTimePresentation';
import { originLabels } from '../../api/records';
import { AnalyteChart } from '../../components/AnalyteChart';
import { DictionarySearchField } from '../../components/DictionarySearchField';
import analyteListStyles from './AnalytePage.css?inline';

const reasons: Record<string, string> = { value_unknown: '原值无法确定', unit_unknown: '单位待补充', unit_mismatch: '原始单位不同', method_unknown: '检测方法待补充', method_mismatch: '检测方法不同', qualified_value: '包含限定值，不能作为精确结果比较', time_unknown: '医学时间待补充', time_ambiguous: '当地时间存在歧义，无法确定先后', time_invalid: '当地时间无效，不能比较', time_unresolved: '准确时间尚未解析，不能比较', precision_mismatch: '医学时间精度不同', same_time: '同一医学时间的结果需要核对', timezone_unconfirmed: '时区尚未确认一致' };
const qualifiers = { eq: '=', lt: '<', le: '≤', gt: '>', ge: '≥' };
const verificationLabels: Record<string, string> = { verified: '已核对', pending: '待核对', revoked: '已撤销', needs_correction: '需补正', unverified: '未核对' };

export function AnalytePage() {
  const access = useAccess();
  const navigate = useNavigate();
  const { analyteId } = useParams();
  const [sourceScope, setSourceScope] = useState<string | null>(null);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useAnalyteResource(access.principalId, access.patientId, analyteId ?? null, canRead);
  const data = canRead ? resource.data : null;
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const scope = JSON.stringify([access.principalId, access.patientId, analyteId]);
  const currentName = data?.trend.find(item => item.original_name)?.original_name;
  const versions = data ? [...new Set(data.trend.map(item => item.record_version_id))] : [];
  const sourceOpen = sourceScope === scope && !!data && versions.length > 0;
  useEffect(() => { if (!data) setSourceScope(null); }, [data, scope]);
  return <UI2FamilyFrame title="历次检验" screenId="UI2-07" note="只有可比较的独立采样显示变化。" backTo="/records"
    overlay={sourceOpen && <UI2Sheet title="查看原始资料" onClose={() => setSourceScope(null)}>
      <p>按每次结果的准确版本读取原始资料和出处。</p>
      {versions.map(versionId => <ContextLink key={versionId} className="list-row" to={`/records/versions/${versionId}`}>
        <span className="row-text"><strong>查看来源版本</strong><small>{medicalSourceTimeLabel(data.trend.find(item => item.record_version_id === versionId)!.medical_time)}</small></span><ChevronRight size={17} />
      </ContextLink>)}
    </UI2Sheet>}>
    <style>{analyteListStyles}</style>
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={resource.loading || access.loading} error={resource.error} onRetry={resource.reload} />
    <section className="card form-card">
      <p>当前项目：{currentName ?? analyteId ?? '尚未选择'}</p>
      <DictionarySearchField category="laboratory" disabled={!canRead}
        selected={null} onSelect={value => {
          if (value) navigate(`/records/analytes/${encodeURIComponent(value.concept_code)}`);
        }} />
      {data?.trend.length === 1 && <div className="rich-single-result"><span className="badge neutral">原始结果</span>
        <div className="rich-record-value">{data.trend[0].value_state === 'unknown' ? '无法确定' : <>{data.trend[0].qualifier ? qualifiers[data.trend[0].qualifier] : ''}{data.trend[0].raw_numeric ?? data.trend[0].raw_text ?? '原值未提供'}{data.trend[0].raw_unit ? ` ${data.trend[0].raw_unit}` : '（单位待补充）'}</>}</div>
        <p>医学时间：{medicalSourceTimeLabel(data.trend[0].medical_time)}</p>
        <p>{originLabels[data.trend[0].origin_type]}{data.trend[0].source_organization ? ` · ${data.trend[0].source_organization}` : ''}</p>
        {data.trend[0].value_state === 'invalid' && <p>原值状态：需要更正</p>}
      </div>}
      <span className="badge neutral">原始变化</span>
      {data && !data.trend.some(item => item.direction !== null) && <div className="empty"><span className="empty-icon"><History size={54} /></span><h3>尚无可比较的结果</h3><p>需要至少两次时间、单位与方法符合条件的独立采样。</p></div>}
      {data && data.trend.some(item => item.direction !== null) && <AnalyteChart key={scope} trend={data.trend} ui2 />}
    </section>
    <section className="card">
      <ContextLink className="list-row" to="/records/period"><span className="row-text"><strong>查看观察时段</strong><small>采样时间与上传时间分别保存</small></span><ChevronRight size={17} /></ContextLink>
      <button className="list-row" disabled={!data || !versions.length} onClick={() => setSourceScope(scope)}><span className="row-text"><strong>查看原始资料</strong></span><ChevronRight size={17} /></button>
    </section>
    {data && data.trend.length > 0 && <section className="card form-card rich-analyte-results"><h2>历次结果</h2>
      {data.trend.map((item, index) => <details key={item.field_id} className="rich-analyte-result">
        <summary>
          <span className="rich-analyte-result-time">{medicalSourceTimeLabel(item.medical_time)}</span>
          <strong className="rich-analyte-result-value">{item.qualifier ? qualifiers[item.qualifier] : ''}{item.raw_numeric ?? item.raw_text ?? '原值未提供'}{item.raw_unit ? ` ${item.raw_unit}` : '（单位待补充）'}</strong>
          <ChevronRight size={15} />
          {item.value_state !== 'known' && <span className="rich-analyte-result-state">原值状态：{{ unknown: '无法确定', invalid: '需要更正' }[item.value_state]}</span>}
          <span className="rich-analyte-result-comparison">{item.direction === 'up' ? '较前次上升' : item.direction === 'down' ? '较前次下降' : item.direction === 'same' ? '与前次相同' : index === 0 ? '没有更早的可比较结果' : item.comparison_reasons.length ? item.comparison_reasons.map(reason => reasons[reason]).join('；') : '不满足比较要求'}</span>
          {index === 0 && item.direction === null && item.comparison_reasons.length > 0 && <span className="rich-analyte-result-comparison">{item.comparison_reasons.map(reason => reasons[reason]).join('；')}</span>}
        </summary>
        <div className="pair"><span>原值状态</span><strong>{{ known: '已提供', unknown: '无法确定', invalid: '需要更正' }[item.value_state]}</strong></div>
        <div className="pair"><span>原项目名称</span><strong>{item.original_name ?? '未提供'}</strong></div>
        {item.raw_text !== null && <div className="pair"><span>原文</span><strong>{item.raw_text}</strong></div>}
        <div className="pair"><span>医学时间</span><strong>{medicalSourceTimeLabel(item.medical_time)}</strong></div>
        <div className="pair"><span>检测方法</span><strong>{item.method ?? '待补充'}</strong></div>
        <div className="pair"><span>资料来源</span><strong>{originLabels[item.origin_type]}</strong></div>
        <div className="pair"><span>来源机构</span><strong>{item.source_organization ?? '未提供'}</strong></div>
        <div className="pair"><span>专业核对</span><strong>{item.verification_states.length ? item.verification_states.map(state => verificationLabels[state.status]).join('、') : '未核对'}</strong></div>
        {item.comparison?.kind === 'approved_mapping' && <details>
          <summary>单位换算依据</summary>
          <p>本次 {item.comparison.current_value} {item.comparison.unit}；前次 {item.comparison.previous_value} {item.comparison.unit}。原值仍按报告保存。</p>
          <p>映射配置：{item.comparison.config_id}；版本摘要：{item.comparison.config_checksum}</p>
          <p>本次映射：{item.comparison.current_mapping_id}；前次映射：{item.comparison.previous_mapping_id}</p>
        </details>}
        <ContextLink className="text-action" to={`/records/versions/${item.record_version_id}`}>查看来源版本<ChevronRight size={17} /></ContextLink>
      </details>)}
    </section>}
  </UI2FamilyFrame>;
}
