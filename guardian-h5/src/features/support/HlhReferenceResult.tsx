import type { HlhReferenceResult as HlhResult, HlhState } from '../../../../frontend-design/v2-types';

const names: Record<string, string> = {
  fever: '发热', splenomegaly: '脾大', cytopenias: '血细胞减少（至少两系）',
  lipids_or_fibrinogen: '空腹甘油三酯或纤维蛋白原', hemophagocytosis: '指定组织噬血现象',
  nk_activity: 'NK 细胞活性低或缺失', ferritin: '铁蛋白', soluble_cd25: '可溶性 CD25',
  hemoglobin: '血红蛋白（已核对年龄）', age_at_hemoglobin: '采样年龄依据', platelet_count: '血小板',
  neutrophil_count: '中性粒细胞', triglycerides_numeric: '甘油三酯数值', fasting: '同次采样空腹依据',
  fasting_triglycerides: '合格空腹甘油三酯', fibrinogen: '纤维蛋白原', no_malignancy_evidence: '无恶性肿瘤证据限定',
};
const stateLabel = (state: HlhState) => ({ met: '已满足', not_met: '未满足', unknown: '未知，需补充资料' })[state];
const resultLabel = { criteria_supported: '参考路径条件已满足', criteria_not_supported: '当前资料未满足参考路径',
  insufficient: '资料不足，暂不能判定', not_applicable: '当前适用条件不符' };

export function HlhReferenceResult({ result }: { result: HlhResult | null }) {
  return <section aria-label="HLH-2004 独立参考评估" className="card form-card">
    <div className="eyebrow">HLH-2004 · 独立参考</div>
    <h2>{result ? resultLabel[result.reference_result] : '尚无有效 HLH-2004 参考结果'}</h2>
    <p className="note">此处核对参考条件，不能替代医生诊断或治疗决定。未满足标准不排除 HLH 或 MAS；缺少的检查保留为未知。</p>
    {result && <>
      <div className="pair"><span>八项条件已满足</span><strong>{result.criteria_met_lower_bound} / 8</strong></div>
      <div className="pair"><span>含未知项的可能上限</span><strong>{result.criteria_met_upper_bound} / 8</strong></div>
      <p className="muted">上限仅表示未知项补全后最多可满足的条件数，不代表发生概率。两项复合条件各只计一项。</p>
      <div className="pair"><span>独立分子依据路径</span><strong>{stateLabel(result.molecular_condition.state)}</strong></div>
      <p className="muted">采用经专业核对的“与 HLH 一致的分子诊断”依据；分子路径与八项中至少五项为替代路径。</p>
      <ol aria-label="八项参考条件">{result.criteria.map(item => <li key={item.code}>
        <div className="pair"><span>{names[item.code]}</span><strong>{stateLabel(item.state)}</strong></div>
        {!!Object.keys(item.subconditions).length && <details><summary>查看子条件</summary>
          {Object.entries(item.subconditions).map(([code, value]) => <p key={code}>{names[code] ?? code}：{stateLabel(value.state)}</p>)}
        </details>}
        {(item.reason_codes.length > 0 || item.fact_version_ids.length > 0) && <details><summary>查看本项依据与限制</summary>
          {item.reason_codes.map(code => <p key={code}>{code}</p>)}
          {item.fact_version_ids.map(id => <p key={id}>采用事实版本：{id}</p>)}
        </details>}
      </li>)}</ol>
      <details><summary>规则版本与来源追溯</summary>
        <p>原始表格：{result.source_table === 'histiocyte-society-2004-table-2' ? 'Histiocyte Society 2004 · 表 2' : 'Henter 2007 · 表 I'}</p>
        <p>来源版本：{result.source_revision_id}</p><p>规则修订：{result.policy_revision_id}</p>
        <p>缺项与计数策略：{result.semantics_version}</p><p>规则校验：{result.policy_checksum}</p>
        <p>输入校验：{result.input_checksum}</p><p>资料截止：{result.known_at}</p>
        <p>这是受控支持中的软件参考输出，不构成临床启用批准。</p>
      </details>
    </>}
  </section>;
}
