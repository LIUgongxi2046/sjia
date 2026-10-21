import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ChevronRight as ArrowRight, ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import { Bell, ShieldCheck, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useFamilyResource } from '../../api/useFamilyResource';
import { markAlertRead } from '../../api/endpoints';
import type { AlertDetailView, AlertItem, HomeView } from '../../api/endpoints';
import { assessmentText } from '../home/assessment';
import { useNotificationDelivery } from './useNotificationDelivery';

function Patient() {
  const access = useAccess(), navigate = useNavigate();
  return <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{access.relations.find(relation => relation.patient_id === access.patientId)?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>;
}

export function UI2AlertsPage() {
  const access = useAccess(), navigate = useNavigate();
  const canRead = !!access.principalId && !!access.patientId && !access.error && !access.loading;
  const home = useFamilyResource<HomeView>(canRead ? `/api/patients/${access.patientId}/home` : null, 'HomeView');
  const list = useFamilyResource<{ alerts: AlertItem[] }>(canRead ? `/api/patients/${access.patientId}/alerts` : null, 'AlertList');
  const text = home.data ? assessmentText(home.data) : null;
  const error = home.error ?? list.error, loading = home.loading || list.loading;
  return <UI2FamilyFrame title="预警" screenId="UI2-11" note="明确当前有效性与查看状态。" backTo="/home">
    <UI2ReadState loading={loading} error={error} onRetry={() => { home.reload(); list.reload(); }} />
    {canRead && !loading && !error && home.data && list.data && <><Patient />
      <section className="home-alert"><div className="eyebrow"><ShieldCheck size={24} />当前服务状态</div><h2>{text?.heading}</h2><p>{text?.body}</p>
        <button className="text-action" onClick={() => navigate(home.data?.current_alert_id ? `/alerts/${home.data.current_alert_id}` : '/home')}>查看当前状态与依据<ArrowRight size={16} /></button></section>
      <div className="section-heading"><h2>提示历史</h2></div>
      {!list.data.alerts.length ? <section className="card"><div className="empty"><span className="empty-icon"><Bell size={54} /></span><h3>尚无获准提示</h3><p>此状态不代表没有风险。</p><button className="text-action" onClick={() => navigate('/alerts/current')}>查看详情与依据<ArrowRight size={16} /></button></div></section>
        : list.data.alerts.map(alert => <section className="card" key={alert.alert_id}><ContextLink className="list-row" to={`/alerts/${alert.alert_id}`}><span className="row-icon"><Bell size={30} /></span><span className="row-text"><strong>{alert.validity === 'valid' ? '提示详情' : '已失效提示'}</strong><small>{eventTimeLabel(alert.created_at)} · {alert.read_at ? '已读' : '未读'}</small></span><ChevronRight size={17} /></ContextLink></section>)}
    </>}
  </UI2FamilyFrame>;
}

export function UI2AlertDetailPage() {
  const access = useAccess(), navigate = useNavigate(), { alertId } = useParams();
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  const canRead = visible && !!access.principalId && !!access.patientId && !access.error && !access.loading;
  const current = alertId === 'current';
  const home = useFamilyResource<HomeView>(canRead && current ? `/api/patients/${access.patientId}/home` : null, 'HomeView');
  const target = current ? home.data?.current_alert_id : alertId;
  const resource = useFamilyResource<AlertDetailView>(canRead && target ? `/api/alerts/${target}` : null, 'AlertDetailView');
  const [readAt, setReadAt] = useState<string | null>(null), [readError, setReadError] = useState<Error | null>(null);
  const [reading, setReading] = useState(false), [attempt, setAttempt] = useState(0), [clock, setClock] = useState(Date.now());
  const data = resource.data;
  const contentRef = useRef<HTMLElement>(null);
  const notification = useNotificationDelivery(data && (data.read_at || readAt) ? data : null, access.patientId, contentRef, attempt);
  const contact = data?.content?.contact;
  useEffect(() => {
    if (contact?.state !== 'available' || !contact.valid_until) return;
    const remaining = Date.parse(contact.valid_until) - Date.now();
    if (remaining <= 0) { setClock(Date.now()); return; }
    const timer = window.setTimeout(() => { setClock(Date.now()); resource.reload(); }, Math.min(remaining + 1, 2147483647));
    return () => window.clearTimeout(timer);
  }, [contact?.state, contact?.valid_until, resource.reload]);
  useEffect(() => {
    const controller = new AbortController();
    let observer: IntersectionObserver | null = null;
    setReadAt(null); setReadError(null); setReading(false);
    if (data?.validity === 'valid' && data.content_state === 'available' && !data.read_at && contentRef.current) {
      let submitted = false;
      observer = new IntersectionObserver(entries => {
        if (submitted || controller.signal.aborted || document.visibilityState !== 'visible' || !entries.some(row => row.isIntersecting)) return;
        submitted = true; observer?.disconnect(); setReading(true);
        void markAlertRead(data.alert_id, access.csrfToken ?? undefined, controller.signal).then(result => {
        if (!controller.signal.aborted) {
          if (!result.read_at) throw new Error('服务尚未保存本人的阅读时间。');
          setReadAt(result.read_at);
        }
      }).then(undefined, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted) setReadError(failure);
      }).finally(() => { if (!controller.signal.aborted) setReading(false); });
      }, { threshold: 0.1 });
      observer.observe(contentRef.current);
    }
    const hidden = () => { if (document.visibilityState !== 'visible') { controller.abort(); observer?.disconnect(); setReadAt(null); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.abort(); observer?.disconnect(); document.removeEventListener('visibilitychange', hidden); };
  }, [data, access.csrfToken, attempt]);
  const contactAvailable = contact?.state === 'available' && !!contact.valid_until && Date.parse(contact.valid_until) > Math.max(clock, Date.now());
  return <UI2FamilyFrame title="预警详情" screenId="UI2-12" note="批准文字、依据、时效与联系入口。" backTo="/alerts">
    <UI2ReadState loading={resource.loading || home.loading} error={resource.error ?? home.error} onRetry={() => { home.reload(); resource.reload(); }} />
    {canRead && current && home.data && !home.data.current_alert_id && !home.loading && !home.error && <><Patient />
      <section className="home-alert"><div className="eyebrow"><ShieldCheck size={24} />当前服务状态</div><h2>{assessmentText(home.data).heading}</h2><p>{assessmentText(home.data).body}</p></section>
      <div className="section-heading"><h2>提示依据</h2></div><section className="card form-card"><div className="pair"><span>评估时间</span><strong>{home.data.assessed_at ? eventTimeLabel(home.data.assessed_at) : '尚无有效评估'}</strong></div>
        <div className="pair"><span>资料来源</span><strong>当前没有获准提示依据</strong></div><div className="pair"><span>专业核对</span><strong>查看各项资料的当前核对状态</strong></div><div className="pair"><span>当前有效性</span><strong>尚无有效提示</strong></div></section>
      <section className="card"><button className="list-row" onClick={() => navigate('/records/period')}><span className="row-text"><strong>查看观察时段</strong></span><ChevronRight size={17} /></button>
        <button className="list-row" onClick={() => navigate('/archives/source')}><span className="row-text"><strong>查看原始资料</strong></span><ChevronRight size={17} /></button>
        <button className="list-row" onClick={() => navigate('/summaries')}><span className="row-text"><strong>准备就医摘要</strong></span><ChevronRight size={17} /></button></section>
      <div className="note blue"><ShieldCheck size={17} /><p>联系信息尚未提供。阅读提示不代表已经获得诊疗。</p></div>
    </>}
    {canRead && !resource.loading && !resource.error && data && <><Patient />
      <section className="home-alert" ref={contentRef}><div className="eyebrow"><ShieldCheck size={24} />当前服务状态</div>
        <h2>{data.validity === 'invalid' ? '提示已失效' : data.content_state === 'configuration_pending' ? '内容尚未发布' : '获准提示内容'}</h2>
        <p>{data.validity === 'invalid' ? '资料、核对或发布版本已经变化，请查看首页当前提示。' : data.content?.approved_text ?? '提示正文需要有效发布配置，当前无法读取。'}</p>
      </section><div className="section-heading"><h2>提示依据</h2></div>
      <section className="card form-card"><div className="pair"><span>提示时间</span><strong>{eventTimeLabel(data.created_at)}</strong></div>
        <div className="pair"><span>评估时间</span><strong>{data.assessed_at ? eventTimeLabel(data.assessed_at) : '尚无当前获准评估时间'}</strong></div>
        <div className="pair"><span>专业核对</span><strong>核对状态见准确来源版本</strong></div>
        <div className="pair"><span>当前有效性</span><strong>{data.validity === 'valid' ? '有效' : '已失效'}</strong></div><div className="pair"><span>本人阅读时间</span><strong>{readAt || data.read_at ? eventTimeLabel((readAt || data.read_at)!) : reading ? '正在保存' : '尚未记录'}</strong></div>
        <p>阅读记录不表示已经完成诊疗。</p>{readError && <><p role="alert">{readError.message}</p><button className="text-action" onClick={() => setAttempt(value => value + 1)}>重新保存阅读状态<ArrowRight size={16} /></button></>}
        {notification.record && <div className="pair"><span>本人站内通知</span><strong>{notification.record.delivery_status === 'delivered' ? '已送达本人浏览器'
          : notification.record.delivery_status === 'accepted' ? '消息已受理，送达待确认' : notification.record.delivery_status === 'not_sent' ? '尚未发送' : '查看当前发送记录'}</strong></div>}
        {notification.error && <><p role="alert">{notification.error}</p><button className="text-action" onClick={() => setAttempt(value => value + 1)}>重新查询本人通知</button></>}
      </section>
      {data.validity === 'valid' && data.content_state === 'available' && data.content && <>
        <section className="card form-card"><h3>联系信息</h3>{contactAvailable ? <><p>{contact.contact_name}</p><p>{contact.channel_value}</p>
          {contact.channel === 'phone' && <a className="text-action" href={`tel:${encodeURIComponent(contact.channel_value ?? '')}`}>拨打联系电话</a>}
          {contact.channel === 'email' && <a className="text-action" href={`mailto:${encodeURIComponent(contact.channel_value ?? '')}`}>发送联系邮件</a>}
          {contact.channel === 'in_app' && <p>联系渠道为站内消息。</p>}</> : <p>当前没有有效联系信息。</p>}</section>
        <section className="card form-card"><h3>资料来源</h3>{data.content.source_refs.length ? data.content.source_refs.map(source => <ContextLink className="list-row" key={`${source.record_version_id}:${source.field_id}`} to={`/records/versions/${source.record_version_id}`}><span className="row-text"><strong>查看来源版本 {source.source_version}</strong><small>当前获准的来源字段</small></span><ChevronRight size={17} /></ContextLink>) : <p>当前权限范围内没有可读取的来源字段。</p>}
          <details><summary>内容版本</summary><p>{data.content.content_version}</p></details></section>
      </>}
      <section className="card"><button className="list-row" onClick={() => navigate('/records/period')}><span className="row-text"><strong>查看观察时段</strong></span><ChevronRight size={17} /></button>
        <button className="list-row" onClick={() => navigate('/archives/source')}><span className="row-text"><strong>查看原始资料</strong></span><ChevronRight size={17} /></button>
        <button className="list-row" onClick={() => navigate('/summaries')}><span className="row-text"><strong>准备就医摘要</strong></span><ChevronRight size={17} /></button></section>
      <div className="note blue"><ShieldCheck size={17} /><p>阅读提示不代表已经获得诊疗。</p></div>
      {data.validity === 'invalid' && <button className="button secondary" onClick={() => navigate('/home')}>查看当前提示</button>}
    </>}
  </UI2FamilyFrame>;
}
