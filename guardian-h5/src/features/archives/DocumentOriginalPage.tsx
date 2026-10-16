import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useNavigate } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import { FileText, Image, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useDocumentSource } from '../../api/useDocumentSource';
import { documentTimeLabel } from './documentTimeLabel';
import { ArchiveOriginals } from './ArchiveOriginals';
import { documentLabels } from './documentLabels';

const labels = ['文书类型', '来源医院', '原件版本', '文书形成时间', '平台取得时间', '允许用途', '副本复核状态'];
const purposes: Record<string, string> = { family_observation: '家庭资料', external_recognition: '外部识别', format_evaluation: '功能测试', workflow_evaluation: '流程评价', clinical_evaluation: '临床评价', competition_display: '比赛展示', export: '获准导出' };
const copyStates: Record<string, string> = { pending_review: '待复核', approved: '已保存批准状态', rejected: '未通过', revoked: '已撤销', disposed: '已处置' };

export function DocumentOriginalPage({ documentId, versionId }: { documentId: string; versionId?: string | null }) {
  const access = useAccess(), navigate = useNavigate();
  const ready = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const resource = useDocumentSource(ready ? access.patientId : null, ready ? access.principalId : null, documentId, versionId);
  const item = ready ? resource.data : null;
  const value = item?.document;
  const values = item && value ? [documentLabels[value.document_type], value.organization ?? '来源医院未知',
    item.pages.map(page => `文书页序 ${page.page_order}：${page.object_version}`).join('；'), documentTimeLabel(value.documented_time),
    item.pages.map(page => `文书页序 ${page.page_order}保存于 ${eventTimeLabel(page.created_at)}`).join('；'),
    [...new Set(item.pages.flatMap(page => page.use_authorizations.map(authorization => `${purposes[authorization.purpose] ?? authorization.purpose}（${authorization.environment}）`)))].join('；') || '尚未取得有效用途授权',
    [...new Set(item.pages.flatMap(page => page.purpose_copies.map(copy => `${purposes[copy.purpose] ?? copy.purpose}：${copyStates[copy.status]}${copy.review_id ? copy.revoked_at ? '；复核已撤销' : '；已保存复核记录' : '；尚无复核记录'}`)))].join('；') || '尚无用途副本复核记录'] : labels.map(() => '待读取');
  return <UI2FamilyFrame title="原始资料" screenId="UI2-33" note="显示来源版本、用途与文书页面。" backTo="/archives">
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={access.loading || resource.loading} error={resource.error} onRetry={() => { void access.refresh(); resource.reload(); }} />
    {resource.data && !item && <section className="status-panel error" role="alert"><h2>当前文书尚不能读取</h2><p>需要有效患儿关系及完整来源读取权限。</p></section>}
    <section className="card source-view"><Image size={58} /><h2>{item ? '已保存原始文书' : '尚未选择原始文书'}</h2><p>完整原件与提取内容共同保留，支持按页面查看。</p>{item ? <ArchiveOriginals key={value!.version_id} pages={item.pages} /> : <button className="button secondary" disabled>选择照片</button>}</section>
    <section className="card form-card">{labels.map((label, index) => <div className="pair" key={label}><span>{label}</span><strong>{values[index]}</strong></div>)}</section>
    <section className="card">{value ? <ContextLink className="list-row" to={`/archives/grouping?document=${encodeURIComponent(value.document_id)}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>逐项内容与时间</strong></span><ChevronRight size={17} /></ContextLink> : <button className="list-row" disabled><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>逐项内容与时间</strong></span><ChevronRight size={17} /></button>}
      {value ? <ContextLink className="list-row" to={`/archives/grouping?document=${encodeURIComponent(value.document_id)}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看文书归组</strong></span><ChevronRight size={17} /></ContextLink> : <button className="list-row" disabled><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看文书归组</strong></span><ChevronRight size={17} /></button>}
    </section>
    {value && <details><summary>原文依据与准确版本</summary><p>引用文书版本 {value.version}；准确版本 {value.version_id}。</p><p>确认原文：{value.confirmed_text ?? '尚无确认文本'}</p><p>文书形成时间保留已记录精度。确认文本对应完整文书；原件按保存页序查看。</p></details>}
  </UI2FamilyFrame>;
}
