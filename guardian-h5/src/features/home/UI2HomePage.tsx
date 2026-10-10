import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowRight, Plus } from 'lucide-react';
import { Camera, Layers, Pill, ShieldCheck, Thermometer } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { UI2Sheet } from '../../components/UI2Frame';
import { useAccess } from '../../state/access';
import { navigationMemory } from '../../state/navigation';
import { useFamilyResource } from '../../api/useFamilyResource';
import { searchRecords } from '../../api/endpoints';
import type { AlertDetailView, HomeView } from '../../api/endpoints';
import { fieldValue, timeLabel } from '../../api/records';
import type { RecordFieldView } from '../../api/records';
import { assessmentText } from './assessment';
import { HealthArchive } from './HealthArchive';
import child from '../../../../ui-delivery/assets/2x/child-neutral-front@2x.png';

export function UI2HomePage() {
  const access = useAccess(), navigate = useNavigate();
  const canRead = !!access.principalId && !!access.patientId && !access.error && !access.loading;
  const home = useFamilyResource<HomeView>(canRead ? `/api/patients/${access.patientId}/home` : null, 'HomeView');
  const alert = useFamilyResource<AlertDetailView>(canRead && home.data?.current_alert_id ? `/api/alerts/${home.data.current_alert_id}` : null, 'AlertDetailView');
  const [observations, setObservations] = useState<Array<RecordFieldView | null> | null>(null);
  const [observationError, setObservationError] = useState<Error | null>(null);
  const [patientSelectionError, setPatientSelectionError] = useState<Error | null>(null);
  const [patientSelectionBusy, setPatientSelectionBusy] = useState(false);
  const [requestedPatientId, setRequestedPatientId] = useState<string | null>(null);
  const [reload, setReload] = useState(0), [imageFailed, setImageFailed] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(navigationMemory.archive.open);
  const closeArchive = useCallback(() => { navigationMemory.archive.open = false; setArchiveOpen(false); }, []);
  const changePatient = useCallback(async (id: string) => {
    setRequestedPatientId(id);
    setPatientSelectionError(null);
    setPatientSelectionBusy(true);
    try {
      await access.selectPatient(id);
    } catch (failure) {
      setPatientSelectionError(failure instanceof Error ? failure : new Error('当前患儿选择未完成。'));
      setPatientSelectionBusy(false);
    }
  }, [access.selectPatient]);
  const retryPatientSelection = useCallback(() => {
    if (requestedPatientId) void changePatient(requestedPatientId);
  }, [changePatient, requestedPatientId]);
  useEffect(() => {
    const controller = new AbortController();
    setObservations(null); setObservationError(null);
    if (canRead && access.patientId) {
      const patient = access.patientId;
      void Promise.all(['temp', 'rash', 'joint_discomfort'].map(async code => {
        let cursor: string | null = null;
        do {
          const result = await searchRecords(patient, { kind: 'observation', original_name: code, limit: 100, cursor }, { signal: controller.signal });
          const field = result.records.flatMap(record => record.detail.fields).find(field => field.field_code === code);
          if (field) return field;
          cursor = result.next_cursor;
        } while (cursor && !controller.signal.aborted);
        return null;
      })).then(result => { if (!controller.signal.aborted) setObservations(result); }, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted) setObservationError(failure);
      });
    }
    return () => controller.abort();
  }, [canRead, access.patientId, reload]);
  const text = home.data ? assessmentText(home.data) : null;
  const active = access.relations.find(relation => relation.patient_id === access.patientId);
  return <UI2FamilyFrame title="首页" screenId="UI2-01" note="当前提示优先，拍照记录紧随其后。" backTo="/home" primary="home"
    overlay={canRead && archiveOpen ? <UI2Sheet title="健康档案" onClose={closeArchive}><HealthArchive /></UI2Sheet> : undefined}>
    <UI2ReadState loading={home.loading} error={home.error} onRetry={home.reload} />
    {canRead && !home.error && home.data && text && <section className="home-alert">
      <div className="eyebrow"><span className="status-dot" />当前预警</div><div className="home-alert-title"><div><h2>{text.heading}</h2>
        {alert.data?.validity === 'valid' && alert.data.content_state === 'available' && alert.data.content ? <p>{alert.data.content.approved_text}</p> : <p>{text.body}</p>}
      </div><ShieldCheck size={52} /></div>
      {alert.error && <UI2ReadState error={alert.error} onRetry={alert.reload} />}
      {alert.loading && <p role="status">正在读取获准提示内容</p>}
      {alert.data?.validity === 'invalid' && <p>当前提示已失效，请查看最新资料。</p>}
      {alert.data?.content_state === 'configuration_pending' && <p>提示内容尚未发布。</p>}
      {home.data.assessed_at && <p>评估时间：{new Date(home.data.assessed_at).toLocaleString('zh-CN')}</p>}
      {home.data.processing_state !== 'idle' && <p role="status">{({ pending: '评估任务正在等待处理', running: '评估任务正在处理', failed: '评估任务处理失败，请查看当前服务状态' })[home.data.processing_state]}</p>}
      <button className="alert-link" onClick={() => navigate(home.data?.current_alert_id ? `/alerts/${home.data.current_alert_id}` : '/alerts/current')}>查看服务状态与资料依据<ArrowRight size={17} /></button>
    </section>}
    <section className="child-card"><div className="child-copy"><span className="eyebrow">孩子的记录</span><h2>每次记录，<br />都有迹可循。</h2>
      <button className="text-action" onClick={() => navigate('/records/period')}>查看观察时段<ArrowRight size={14} /></button></div>
      {!imageFailed ? <img className="child" src={child} alt="通用儿童插画" onError={() => setImageFailed(true)} /> : <button className="text-action" onClick={() => setImageFailed(false)}>重新加载形象</button>}
      <div className="observed-tags">{['体温', '皮疹', '关节表现'].map((label, index) => <button key={label} onClick={() => navigate('/register/observations')}><span>{label}</span>
        <small>{!canRead ? '需要登录并核验权限' : observationError ? '读取失败' : !observations ? '正在读取' : observations[index] ? fieldValue(observations[index]) : '尚无记录'}</small>
        {observations?.[index] && <small>{timeLabel(observations[index].medical_time)}</small>}
      </button>)}</div></section>
    {canRead && <section className="card form-card"><div className="section-heading"><h2>当前患儿</h2><button className="text-action" onClick={() => { navigationMemory.archive.open = true; setArchiveOpen(true); }}>健康档案</button></div>
      <label className="field"><span>患儿关联</span><select value={access.patientId ?? ''} disabled={patientSelectionBusy}
        onChange={event => { void changePatient(event.target.value); }}>{access.relations.filter(relation => relation.status === 'active'
          && relation.actions.includes('read_raw') && Date.parse(relation.valid_from) <= Date.now()
          && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now()))
          .map(relation => <option key={relation.relation_id} value={relation.patient_id}>{relation.display_id}</option>)}</select></label>
      {patientSelectionError && <UI2ReadState requirePatient={false} error={patientSelectionError} onRetry={retryPatientSelection} />}
      {active && <p>{active.display_id} · 查看本人获准的资料</p>}
      {observationError && <UI2ReadState error={observationError} onRetry={() => setReload(value => value + 1)} />}
    </section>}
    <div className="section-heading"><h2>添加资料</h2></div><button className="camera-action" onClick={() => navigate('/register/reports')}><span><Camera size={43} /></span><div><strong>拍照添加报告</strong><small>保留原件，逐项确认</small></div><Plus size={23} /></button>
    <div className="quick-grid"><button onClick={() => navigate('/register/observations')}><Thermometer size={32} /><strong>体温与症状</strong><small>记录当下观察</small></button><button onClick={() => navigate('/register/therapies')}><Pill size={32} /><strong>治疗资料</strong><small>保留实际使用记录</small></button></div>
    <button className="archive-shortcut" onClick={() => navigate('/archives')}><Layers size={29} /><span><strong>整理长期档案</strong><small>住院、门诊和已有报告</small></span><ArrowRight size={18} /></button>
  </UI2FamilyFrame>;
}
