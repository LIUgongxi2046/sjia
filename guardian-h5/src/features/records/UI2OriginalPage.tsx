import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import { FileText, Image, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { PrivateSourceGallery } from '../../components/PrivateSourceGallery';
import { useAccess } from '../../state/access';
import { useRecordResource } from '../../api/useRecordResource';
import { useOriginalSource } from '../../api/useOriginalSource';
import { timeLabel, type RecordView } from '../../api/records';
import { DocumentOriginalPage } from '../archives/DocumentOriginalPage';
import { PageOriginalPage } from '../archives/PageOriginalPage';

const labels = ['文书类型', '来源医院', '原件版本', '文书形成时间', '平台取得时间', '允许用途', '副本复核状态'];
const purposes: Record<string, string> = { family_observation: '家庭资料', external_recognition: '外部识别', format_evaluation: '功能测试', workflow_evaluation: '流程评价', clinical_evaluation: '临床评价', competition_display: '比赛展示', export: '获准导出' };
const copyStates: Record<string, string> = { pending_review: '待复核', approved: '已保存批准状态', rejected: '未通过', revoked: '已撤销', disposed: '已处置' };

export function UI2OriginalPage() {
  const access = useAccess();
  const [params] = useSearchParams();
  const versionId = params.get('version');
  const documentId = params.get('document');
  const documentVersionId = params.get('document_version');
  const pageId = params.get('page');
  if (pageId && !versionId && !documentId) return <PageOriginalPage key={JSON.stringify([access.principalId, access.patientId, pageId])} pageId={pageId} />;
  if (documentId && !versionId) return <DocumentOriginalPage key={JSON.stringify([access.principalId, access.patientId, documentId, documentVersionId])} documentId={documentId} versionId={documentVersionId} />;
  return <OriginalContent key={JSON.stringify([access.principalId, access.patientId, versionId, documentId])} versionId={versionId} documentId={documentId} />;
}

function OriginalContent({ versionId, documentId }: { versionId: string | null; documentId: string | null }) {
  const access = useAccess();
  const navigate = useNavigate();
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const recordResource = useRecordResource<RecordView>(canRead && versionId ? `/api/record-versions/${versionId}` : null,
    access.patientId, 'RecordView', versionId, 'record_version_id');
  const record = canRead ? recordResource.data : null;
  const resource = useOriginalSource(record);
  const source = record ? resource.data : null;
  const files = source?.originals.filter(file => file.status === 'available' && ['image/png', 'image/jpeg', 'image/webp'].includes(file.media_type)) ?? [];
  const documents = source ? [...new Map(source.originals.flatMap(file => file.documents).map(document => [document.document_version_id, document])).values()] : [];
  const sourceMismatch = !!source && !!documentId && !documents.some(value => value.document_id === documentId);
  const shownSource = sourceMismatch ? null : source;
  const canChoose = canRead && relation?.status === 'active' && relation.actions.includes('write_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const values: string[] = shownSource ? [
    documents.length ? documents.map(document => document.document_type).join('；') : '尚无确认的文书分类',
    [...new Set([shownSource.source_organization, ...documents.map(document => document.source_organization)].filter(value => value !== null))].join('；') || '来源医院未知',
    shownSource.originals.length ? shownSource.originals.map(file => `附图 ${file.ordinal}：${file.object_version}`).join('；') : '未附原件',
    documents.length ? documents.map(document => timeLabel(document.documented_time)).join('；') : '文书形成时间未知',
    shownSource.originals.length ? shownSource.originals.map(file => `附图 ${file.ordinal}保存于 ${eventTimeLabel(file.created_at)}`).join('；') : '未附原件',
    shownSource.originals.some(file => file.use_authorizations.length) ? [...new Set(shownSource.originals.flatMap(file => file.use_authorizations.map(authorization => `${purposes[authorization.purpose] ?? authorization.purpose}（${authorization.environment}）`)))].join('；') : '尚未取得有效用途授权',
    shownSource.originals.some(file => file.purpose_copies.length) ? shownSource.originals.flatMap(file => file.purpose_copies.map(copy => `${purposes[copy.purpose] ?? copy.purpose}：${copyStates[copy.status]}${copy.review_id ? copy.revoked_at ? '；复核已撤销' : '；已保存复核记录' : '；尚无复核记录'}`)).join('；') : '尚无用途副本复核记录',
  ] : labels.map(() => '待读取');
  return <UI2FamilyFrame title="原始资料" screenId="UI2-33" note="显示来源版本、用途与文书页面。" backTo={record ? `/records/${record.record_id}` : '/records'}>
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={recordResource.loading || resource.loading} error={recordResource.error ?? resource.error} onRetry={() => { recordResource.reload(); resource.reload(); }} />
    {sourceMismatch && <section className="status-panel error" role="alert"><h2>原文依据与当前文书不一致</h2><p>当前记录没有引用指定文书，不能采用该来源内容。</p></section>}
    <section className="card source-view"><Image size={58} /><h2>{shownSource ? shownSource.originals.length ? '已保存原始文书' : '本份资料尚未附原件' : '尚未选择原始文书'}</h2><p>完整原件与提取内容共同保留，支持按页面查看。</p>
      {shownSource && files.length ? <PrivateSourceGallery files={files} triggerClassName="button secondary" triggerContent="查看原件" /> : <button className="button secondary" disabled={!canChoose || sourceMismatch} onClick={() => navigate('/register/reports')}>选择照片</button>}
    </section>
    <section className="card form-card">{labels.map((label, index) => <div className="pair" key={label}><span>{label}</span><strong>{values[index]}</strong></div>)}</section>
    <section className="card">
      {record ? <ContextLink className="list-row" to={`/records/versions/${record.record_version_id}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>逐项内容与时间</strong></span><ChevronRight size={17} /></ContextLink>
        : <button className="list-row" disabled><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>逐项内容与时间</strong></span><ChevronRight size={17} /></button>}
      {shownSource && documents.length ? documents.map(value => <ContextLink className="list-row" key={value.document_version_id}
        to={`/archives/grouping?document=${encodeURIComponent(value.document_id)}&version=${encodeURIComponent(shownSource.record_version_id)}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看文书归组</strong></span><ChevronRight size={17} /></ContextLink>)
        : <button className="list-row" disabled><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看文书归组</strong></span><ChevronRight size={17} /></button>}
    </section>
    {shownSource && <details><summary>原件状态与用途依据</summary><p>平台取得时间采用实际原件保存时间，医学时间与文书形成时间分别保留。用途授权与副本复核记录需要共同检查，当前页面不授予外发许可。</p>
      {shownSource.originals.map(file => <section key={file.file_id}><h3>附图 {file.ordinal}</h3><p>原件状态：{file.status}；媒体类型：{file.media_type}；保存页面：{file.pages.map(page => page.ordinal).join('、') || '未知'}。</p>
        {file.use_authorizations.map(authorization => <p key={authorization.authorization_id}>用途依据：{authorization.evidence_ref}；有效期：{authorization.valid_until ? eventTimeLabel(authorization.valid_until) : '未设定结束时间'}。</p>)}
        {file.purpose_copies.map(copy => <p key={copy.copy_id}>副本版本 {copy.version}；身份复核：{copy.identity_result ?? '未知'}；临床内容复核：{copy.clinical_result ?? '未知'}。</p>)}
      </section>)}
      <p>文书归组保留准确原页和文书版本；当前原件与记录来源保持可追溯。</p>
    </details>}
  </UI2FamilyFrame>;
}
