import { ShieldCheck as FlatShieldCheck } from 'lucide-react';
import { Plus, ShieldCheck } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { DispositionTools } from './DispositionTools';
import { useAccess } from '../../state/access';

export function DataUsePage() {
  const access = useAccess();
  return <UI2FamilyFrame title="资料使用说明" screenId="UI2-16" note="用途、保存、删除与外部处理各自说明。" backTo="/account">
    <section className="privacy-intro rich-privacy-intro"><ShieldCheck size={38} /><h2>清楚了解资料如何使用</h2><p>原件、用途副本与访问权限分别管理。</p></section>
    <section className="card form-card rich-disclosures">
      <details><summary>哪些人可以查看<Plus size={18} /></summary><p>本人有效关联与明确岗位授权共同决定资料范围。</p><p>本人身份、有效关联、动作权限和字段范围共同决定访问权限。报告、历史和摘要使用相同的授权检查。</p></details>
      <details><summary>外部识别如何使用资料<Plus size={18} /></summary><p>发送前检查来源用途与必要副本处理证明。</p><p>识别内容需要本人逐项核对，确认结果与专业核对分别保存。</p></details>
      <details><summary>原件与脱敏副本<Plus size={18} /></summary><p>副本独立保存，保留原件关联、处理和人工复核记录。</p><p>原始报告、本人确认与专业核对分别保留，可以通过当前权限查看准确来源。</p></details>
      <details><summary>资料来源与核对状态<Plus size={18} /></summary><p>资料来源记录家庭自报、报告转录或获准临床资料的出处。</p><p>核对状态由授权人员对指定内容核对。家庭登记或转录确认后，资料仍为未核对。</p><p>缺少来源、单位或时间时，保留原始内容并注明待补项目。</p></details>
      <details><summary>保存与删除<Plus size={18} /></summary><p>按批准保存策略处理原件、副本、核对证据及备份。</p><p>具体处理范围及结果需要读取实际申请与处置证明；申请处理完成前，不显示资料已删除。</p></details>
      <details><summary>联系与维护<Plus size={18} /></summary><p>联系方式、用途和有效期由指定人员确认。</p><p>联系信息根据每条有效提示展示。没有取得有效联系方式时，页面会说明当前状态。</p></details>
      <details><summary>记录按什么时间排列<Plus size={18} /></summary><p>体温与症状按发生或测量时间排列，检验按采样时间排列。出具时间与登记时间分别保留。不能确认时间的记录单独列入“时间待补充”。同日多次采样分别保留，同一时间的不同结果需要核对后再比较。</p></details>
      <details><summary>草稿、退出与页面恢复<Plus size={18} /></summary><p>恢复已保存的草稿需要核验本人身份和患儿权限。退出、账号切换或权限失效后清除页面资料。</p></details>
    </section>
    <div className="note blue"><FlatShieldCheck size={17} /><p>申请按当前权限和保存规则处理，结果以逐项处理记录为准。</p></div>
    <DispositionTools key={JSON.stringify([access.principalId, access.patientId])} />
  </UI2FamilyFrame>;
}
