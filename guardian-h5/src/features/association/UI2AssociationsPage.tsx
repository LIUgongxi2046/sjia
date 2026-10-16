import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ChevronRight as ArrowRight, ChevronRight, ShieldCheck } from '../../components/ThreeDIcon';
import childIllustration from '../../../../ui-delivery/identity/master/toddler-boy-autumn.png';
import { Plus, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2Sheet } from '../../components/UI2Frame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useRelationResource } from '../../api/useRelationResource';
import { UI2ApplicationWithdrawal } from './UI2ApplicationWithdrawal';
import { ApplicationDeclarationDetails } from './ApplicationDeclarationDetails';
import type { ApplicationItem, ApplicationView, RelationItem, RelationView } from '../../api/endpoints';

const applicationLabels: Record<ApplicationItem['status'], string> = {
  pending: '待核验', approved: '已通过', rejected: '未通过', withdrawn: '已撤回',
};
const actionLabels: Record<string, string> = {
  read_raw: '查看资料', write_record: '登记资料', correct_record: '更正记录', receive_alert: '接收提示',
};

function activeRelation(relation: RelationItem, now: number) {
  return relation.status === 'active' && Date.parse(relation.valid_from) <= now
    && (relation.valid_until === null || Date.parse(relation.valid_until) > now);
}

function relationLabel(relation: RelationItem, now: number) {
  return activeRelation(relation, now) ? '有效关联' :
    ({ active: '关联当前无效', revoked: '已撤销', expired: '已失效' } as Record<string, string>)[relation.status];
}

function ApplicationDetails({ applicationId, onClose, onChanged }: { applicationId: string; onClose: () => void; onChanged: () => void }) {
  const access = useAccess();
  const canRead = !!access.principalId && !access.loading && !access.error;
  const resource = useRelationResource<ApplicationView>(canRead ? `/api/relation-applications/${applicationId}` : null,
    access.principalId, 'ApplicationView', applicationId);
  const [current, setCurrent] = useState<ApplicationView | null>(null);
  const application = canRead && resource.data ? current && current.application_id === applicationId
    && BigInt(current.version) >= BigInt(resource.data.version) ? current : resource.data : null;
  return <UI2Sheet title="本人申请详情" onClose={onClose}>
    <UI2ReadState requirePatient={false} loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    {application && <><div className="pair"><span>申请状态</span><strong>{applicationLabels[application.status]}</strong></div>
      <div className="pair"><span>保存版本</span><strong>{application.version}</strong></div>
      <div className="pair"><span>申请权限</span><strong>{application.requested_access
        ? ({ read: '查看资料', read_write: '查看与登记', review_decides: '按核验结果确定' } as const)[application.requested_access] : '尚未保存申请权限'}</strong></div>
      <div className="pair"><span>申请时间</span><strong>{eventTimeLabel(application.created_at)}</strong></div>
      <div className="pair"><span>患儿标识</span><strong>{application.patient_id ?? '尚未匹配患儿'}</strong></div>
      <ApplicationDeclarationDetails declaration={application.applicant_declaration} />
      <h3>申请说明及证明依据</h3><p>{application.evidence_ref}</p>
      {application.note !== null && <><h3>补充说明</h3><p>{application.note}</p></>}
      <div className="pair"><span>处理说明</span><strong>{application.reason ?? '尚未提供'}</strong></div>
      <p>申请状态与实际访问权限分别确认。只有核验后的有效关联和授权才能访问患儿资料。</p>
      <UI2ApplicationWithdrawal application={application} onCurrent={value => { setCurrent(value); onChanged(); }} /></>}
    <button className="button secondary" onClick={onClose}>继续查看患儿管理</button>
  </UI2Sheet>;
}

export function UI2AssociationsPage() {
  const access = useAccess();
  const navigate = useNavigate();
  const canRead = !!access.principalId && !access.loading && !access.error;
  const relations = useRelationResource<{ relations: RelationItem[] }>(canRead ? '/api/relations' : null,
    access.principalId, 'RelationList');
  const applications = useRelationResource<{ applications: ApplicationItem[] }>(canRead ? '/api/relation-applications' : null,
    access.principalId, 'ApplicationList');
  const [applicationId, setApplicationId] = useState<string | null>(null);
  const [selectingRelationId, setSelectingRelationId] = useState<string | null>(null);
  const [selectionTarget, setSelectionTarget] = useState<RelationItem | null>(null);
  const [selectionError, setSelectionError] = useState<Error | null>(null);
  const closeApplication = useCallback(() => { setApplicationId(null); applications.reload(); }, [applications.reload]);
  const selectRelation = useCallback(async (relation: RelationItem) => {
    setSelectionTarget(relation);
    setSelectionError(null);
    setSelectingRelationId(relation.relation_id);
    try {
      await access.selectPatient(relation.patient_id);
      navigate('/home');
    } catch (failure) {
      setSelectionError(failure instanceof Error ? failure : new Error('当前患儿选择未完成。'));
      setSelectingRelationId(null);
    }
  }, [access.selectPatient, navigate]);
  const retrySelection = useCallback(() => {
    if (selectionTarget) void selectRelation(selectionTarget);
  }, [selectRelation, selectionTarget]);
  useEffect(() => { if (!canRead) setApplicationId(null); }, [canRead]);
  const items = canRead ? relations.data?.relations : undefined;
  const [clock, setClock] = useState(Date.now());
  const now = Math.max(clock, Date.now());
  useEffect(() => {
    if (!items) return;
    const current = Date.now();
    const next = items.flatMap(relation => [Date.parse(relation.valid_from), ...(relation.valid_until ? [Date.parse(relation.valid_until)] : [])])
      .filter(value => value > current).sort((first, second) => first - second)[0];
    if (next === undefined) return;
    const timer = window.setTimeout(() => { setClock(Date.now()); relations.reload(); }, Math.min(next - current, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [items, relations.reload, clock]);
  return <UI2FamilyFrame title="患儿管理" screenId="UI2-18" note="本人关联独立核验，不因知道患儿标识而取得权限。" backTo="/account"
    overlay={canRead && applicationId && <ApplicationDetails key={`${access.principalId}:${applicationId}`} applicationId={applicationId}
      onChanged={applications.reload} onClose={closeApplication} />}>
    <UI2ReadState requirePatient={false} loading={relations.loading} error={relations.error} onRetry={relations.reload} />
    {selectionError && <UI2ReadState requirePatient={false} error={selectionError} onRetry={retrySelection} />}
    {items && (items.length === 0 ? <section className="card"><div className="empty"><span className="empty-icon"><UsersRound size={54} /></span>
      <h3>尚无可访问的患儿</h3><p>每位监护人分别申请，按获准范围查看与登记资料。</p>
      <button className="text-action" onClick={() => navigate('/associations/new')}>申请关联<ArrowRight size={16} /></button>
    </div></section> : items.map(relation => <div className="rich-relation-group" key={relation.relation_id}>
      <section className="rich-child-identity"><figure><img src={childIllustration} alt="儿童形象示意，不代表患儿的年龄或性别" /><figcaption>形象示意</figcaption></figure>
        <div><span className="badge neutral">{relation.patient_id === access.patientId ? '当前患儿' : '本人关联'}</span>
          <h2>{relation.display_id}</h2><p>{relationLabel(relation, now)}</p>
          {activeRelation(relation, now) && relation.patient_id !== access.patientId && <button className="text-action"
            disabled={selectingRelationId !== null || !access.relations.some(value => value.relation_id === relation.relation_id
              && value.version === relation.version && activeRelation(value, now) && value.actions.includes('read_raw'))}
            onClick={() => { void selectRelation(relation); }}>{selectingRelationId === relation.relation_id ? '正在保存选择' : '选择此患儿'}<ArrowRight size={16} /></button>}
        </div>
      </section>
      <section className="card"><ContextLink className="list-row" to={`/associations/${relation.relation_id}`}><span className="row-icon"><ShieldCheck size={31} /></span><span className="row-text"><strong>关联与权限</strong><small>{activeRelation(relation, now) ? relation.actions.map(action => actionLabels[action]).filter(Boolean).join('、') || '尚无资料访问权限' : '当前关联不可访问资料'}</small></span><ChevronRight size={17} /></ContextLink></section>
    </div>))}
    <button className="button" onClick={() => navigate('/associations/new')}><Plus size={23} />申请关联患儿</button>
    {!canRead && <button className="text-action" onClick={() => navigate('/associations/new')}>查看关联申请入口<ArrowRight size={16} /></button>}
    {canRead && <details className="card form-card"><summary>本人申请</summary>
      <UI2ReadState requirePatient={false} loading={applications.loading} error={applications.error} onRetry={applications.reload} />
      {applications.data?.applications.length === 0 && <p>尚无申请</p>}
      {applications.data?.applications.map(application => <button className="list-row" key={application.application_id} onClick={() => setApplicationId(application.application_id)}>
        <span className="row-text"><strong>{applicationLabels[application.status]}</strong><small>保存版本 {application.version}{application.reason ? ` · ${application.reason}` : ''}</small></span><ChevronRight size={17} />
      </button>)}
    </details>}
    <details className="card form-card"><summary>了解关联状态</summary><dl><dt>待核验</dt><dd>申请已经提交，尚未取得患儿资料访问权限。</dd><dt>有效</dt><dd>按照核验结果提供查看、登记、更正或接收权限。</dd><dt>未通过、已撤销或已失效</dt><dd>不能继续访问对应患儿资料；具体处理方式按核验流程提供。</dd></dl></details>
    <details className="card form-card"><summary>多名监护人如何使用</summary><p>每名监护人与患儿的关联分别记录。本人获得的访问范围，不会自动授予其他监护人。</p><p>切换患儿前需要处理正在编辑的内容，已填写资料不能直接转移到其他患儿。</p></details>
  </UI2FamilyFrame>;
}

export function UI2AssociationDetailPage() {
  const access = useAccess();
  const navigate = useNavigate();
  const { relationId } = useParams();
  const canRead = !!access.principalId && !access.loading && !access.error;
  const resource = useRelationResource<RelationView>(canRead && relationId ? `/api/relations/${relationId}` : null,
    access.principalId, 'RelationView', relationId ?? null);
  const [clock, setClock] = useState(Date.now());
  const relation = canRead ? resource.data : null;
  useEffect(() => {
    if (!relation) return;
    const now = Date.now();
    const next = [Date.parse(relation.valid_from), ...(relation.valid_until ? [Date.parse(relation.valid_until)] : [])]
      .filter(value => value > now).sort((first, second) => first - second)[0];
    if (next === undefined) return;
    const timer = window.setTimeout(() => { setClock(Date.now()); resource.reload(); }, Math.min(next - now, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [relation, resource.reload, clock]);
  const active = relation && activeRelation(relation, Math.max(clock, Date.now()));
  const state = relation ? relationLabel(relation, Math.max(clock, Date.now())) : '待核验';
  const permission = (action: string) => relation ? active && relation.actions.includes(action) ? '已获准' : '未获准' : '待核验';
  return <UI2FamilyFrame title="关联详情" screenId="UI2-20" note="关联和本人访问范围均以当前核验结果为依据。" backTo="/associations">
    <UI2ReadState requirePatient={false} loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    <div className="patient rich-record-patient"><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '尚未选择关联'}</strong><small>本人关联与访问范围</small></span></div>
    <section className={`rich-relation-status${active ? ' active' : ''}`} role="status"><ShieldCheck size={38} /><h2>{relation ? (active ? '关联有效' : state) : '尚未选择关联'}</h2><p>本人权限以当前核验结果为准</p></section>
    <section className="card form-card">
      <div className="pair"><span>患儿标识</span><strong>{relation?.display_id ?? '待核验'}</strong></div>
      <div className="pair"><span>当前状态</span><strong>{state}</strong></div>
    </section>
    <div className="section-heading"><h2>本人权限</h2></div>
    <section className="card form-card rich-relation-permissions">
      <div className="pair"><span>允许查看</span><strong className="badge neutral" data-granted={permission('read_raw') === '已获准'}>{permission('read_raw')}</strong></div>
      <div className="pair"><span>允许登记</span><strong className="badge neutral" data-granted={permission('write_record') === '已获准'}>{permission('write_record')}</strong></div>
      <div className="pair"><span>允许更正</span><strong className="badge neutral" data-granted={permission('correct_record') === '已获准'}>{permission('correct_record')}</strong></div>
      <div className="pair"><span>允许接收提示</span><strong className="badge neutral" data-granted={permission('receive_alert') === '已获准'}>{permission('receive_alert')}</strong></div>
    </section>
    <div className="note blue"><ShieldCheck size={17} /><p>关联撤销后，后续读写和提示接收均需重新核验。</p></div>
    <button className="button secondary" onClick={() => navigate('/associations')}>返回患儿管理</button>
    {relation && <details className="card form-card"><summary>关联变化与版本</summary>
      <div className="pair"><span>关联版本</span><strong>{relation.version}</strong></div>
      <div className="pair"><span>授权版本</span><strong>{relation.auth_epoch}</strong></div>
      <div className="pair"><span>生效时间</span><strong>{eventTimeLabel(relation.valid_from)}</strong></div>
      <div className="pair"><span>有效期限</span><strong>{relation.valid_until ? eventTimeLabel(relation.valid_until) : '未指定期限'}</strong></div>
      <p>页面展示当前读取到的授权动作。每次读取、登记、更正及接收仍需检查本人身份、关联、字段与记录范围以及运行环境。</p>
      <p>关联变化后重新检查权限，历史资料的保存范围按照资料使用规则处理。</p>
    </details>}
  </UI2FamilyFrame>;
}
