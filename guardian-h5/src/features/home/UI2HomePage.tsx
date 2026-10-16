import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowRight } from 'lucide-react';
import { Layers, ShieldCheck } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { UI2Sheet } from '../../components/UI2Frame';
import { useAccess } from '../../state/access';
import { navigationMemory } from '../../state/navigation';
import { useFamilyResource } from '../../api/useFamilyResource';
import { searchRecords } from '../../api/endpoints';
import type { AlertDetailView, HomeView } from '../../api/endpoints';
import { fieldValue, timeLabel } from '../../api/records';
import type { RecordItem } from '../../api/records';
import { observationEntries, recordSnapshotPath, projectHomeObservationEntries, type ObservationReference } from './observationSelection';
import { useCompanionPreference } from '../../state/companionPreference';
import { assessmentText } from './assessment';
import { HealthArchive } from './HealthArchive';
import { HomeCompanion, HomeQuickActions } from '../../components/HomeVisual';

export function UI2HomePage() {
  const access = useAccess(), navigate = useNavigate();
  const { image } = useCompanionPreference();
  const observationScope = `${access.principalId}:${access.patientId}:${access.scopeVersion}`;
  const canRead = !!access.principalId && !!access.patientId && !access.error && !access.loading;
  const home = useFamilyResource<HomeView>(canRead ? `/api/patients/${access.patientId}/home` : null, 'HomeView');
  const noRelations = !!access.principalId && !access.loading && !access.error && !access.patientId
    && access.relations.length === 0 && !home.error;
  const refreshingRelations = useRef(false);
  async function refreshRelations() {
    if (refreshingRelations.current) return;
    refreshingRelations.current = true;
    try { await access.refresh(); }
    finally { refreshingRelations.current = false; }
  }
  const alert = useFamilyResource<AlertDetailView>(canRead && home.data?.current_alert_id ? `/api/alerts/${home.data.current_alert_id}` : null, 'AlertDetailView');
  const [observationResult, setObservationResult] = useState<{ scope: string; selections: Array<ReturnType<typeof projectHomeObservationEntries<ObservationReference>>> } | null>(null);
  const observations = canRead && observationResult?.scope === observationScope ? observationResult.selections : null;
  const [observationError, setObservationError] = useState<Error | null>(null);
  const [patientSelectionError, setPatientSelectionError] = useState<Error | null>(null);
  const [patientSelectionBusy, setPatientSelectionBusy] = useState(false);
  const [requestedPatientId, setRequestedPatientId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
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
    setObservationResult(null); setObservationError(null);
    if (canRead && access.patientId) {
      const patient = access.patientId;
      void Promise.all(['temp', 'rash', 'joint_discomfort'].map(async code => {
        let cursor: string | null = null;
        const records: RecordItem[] = [];
        const seen = new Set<string>();
        do {
          const result = await searchRecords(patient, { kind: 'observation', original_name: code, limit: 100, cursor }, { signal: controller.signal });
          if (controller.signal.aborted) return projectHomeObservationEntries<ObservationReference>([]);
          records.push(...result.records);
          cursor = result.next_cursor;
          if (cursor && seen.has(cursor)) throw new Error('记录分页未完成，请重新读取。');
          if (cursor) seen.add(cursor);
        } while (cursor && !controller.signal.aborted);
        return projectHomeObservationEntries(observationEntries(records, code));
      })).then(result => { if (!controller.signal.aborted) setObservationResult({ scope: observationScope, selections: result }); }, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted) setObservationError(failure);
      });
    }
    return () => controller.abort();
  }, [canRead, access.patientId, observationScope, reload]);
  const text = home.data ? assessmentText(home.data) : null;
  const active = access.relations.find(relation => relation.patient_id === access.patientId);
  function viewObservation(index: number) {
    if (!canRead || !access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion)) {
      navigate(access.principalId ? '/associations' : '/login'); return;
    }
    const selection = observations?.[index].display;
    if (selection && ['single', 'latest'].includes(selection.state)) {
      const entry = selection.entries[0];
      navigate(recordSnapshotPath({ ...entry, fieldId: entry.field.field_id })); return;
    }
    // No record, unknown chronology or a tie opens the corresponding readable
    // category. Creating new information is a separate, explicitly named action.
    navigationMemory.archive.category = index === 0 ? 'temperature' : 'symptoms';
    navigationMemory.archive.time = 'latest';
    navigationMemory.archive.open = true; setArchiveOpen(true);
  }
  return <UI2FamilyFrame title="首页" screenId="UI2-01" note="当前提示优先，拍照记录紧随其后。" backTo="/home" primary="home"
    overlay={canRead && archiveOpen ? <UI2Sheet title="孩子的记录" variant="archive" onClose={closeArchive}><HealthArchive /></UI2Sheet> : undefined}>
    {noRelations ? <section className="status-panel empty rich-read-state" role="status"><h2>还没有关联的孩子</h2>
      <p>添加患儿关联后，就可以按获准范围查看和记录资料。</p>
      <button className="button" onClick={() => navigate('/associations/new')}>添加患儿关联<ArrowRight size={16} /></button>
      <button className="text-action" onClick={() => void refreshRelations()}>刷新关联<ArrowRight size={16} /></button>
    </section> : <UI2ReadState loading={home.loading} error={home.error} onRetry={home.reload} />}
    {canRead && !home.error && home.data && text && <section className="home-alert">
      <div className="eyebrow"><ShieldCheck size={18} />当前预警</div><div className="home-alert-title"><div><h2>{text.heading}</h2>
        {alert.data?.validity === 'valid' && alert.data.content_state === 'available' && alert.data.content ? <p>{alert.data.content.approved_text}</p> : <p>{text.body}</p>}
      </div></div>
      {alert.error && <UI2ReadState error={alert.error} onRetry={alert.reload} />}
      {alert.loading && <p role="status">正在读取获准提示内容</p>}
      {alert.data?.validity === 'invalid' && <p>当前提示已失效，请查看最新资料。</p>}
      {alert.data?.content_state === 'configuration_pending' && <p>提示内容尚未发布。</p>}
      {home.data.assessed_at && <p>评估时间：{eventTimeLabel(home.data.assessed_at)}</p>}
      {home.data.processing_state !== 'idle' && <p role="status">{({ pending: '评估任务正在等待处理', running: '评估任务正在处理', failed: '评估任务处理失败，请查看当前服务状态' })[home.data.processing_state]}</p>}
      <button className="alert-link" onClick={() => navigate(home.data?.current_alert_id ? `/alerts/${home.data.current_alert_id}` : '/alerts/current')}>查看服务状态与资料依据<ArrowRight size={17} /></button>
    </section>}
    <HomeCompanion name={canRead && active ? active.display_id : '孩子的记录'}
      onArchive={() => { if (canRead) { navigationMemory.archive.open = true; setArchiveOpen(true); } else navigate(access.principalId ? '/associations' : '/login'); }}
      companionImage={image} onViewObservation={viewObservation}
      observations={['体温', '皮疹', '关节表现'].map((label, index) => {
        const projection = observations?.[index];
        const selection = projection?.display;
        const entry = selection && ['single', 'latest'].includes(selection.state) ? selection.entries[0] : null;
        return { label, disabled: canRead && (!observations || !!observationError),
          value: noRelations ? '关联后查看' : !canRead ? '需要核验权限' : observationError ? '读取失败' : !selection ? '正在读取' : entry ? fieldValue(entry.field)
            : selection.state === 'empty' ? '尚无获准记录' : selection.state === 'same_time' ? '同时间有多条记录，请查看' : '记录时间无法确定先后，请查看',
          time: entry ? timeLabel(entry.field.medical_time) : undefined,
          context: projection?.scopedToTimed && selection?.state !== 'indeterminate' ? '最近有明确时间的记录' : undefined,
          notice: projection?.scopedToTimed ? `另有 ${projection.pendingTimeCount} 条记录时间待确认` : undefined,
        };
      })}
      patientPicker={canRead ? <label className="field"><span>切换当前患儿</span><select value={access.patientId ?? ''} disabled={patientSelectionBusy}
        onChange={event => { void changePatient(event.target.value); }}>{access.relations.filter(relation => relation.status === 'active'
          && relation.actions.includes('read_raw') && Date.parse(relation.valid_from) <= Date.now()
          && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now()))
          .map(relation => <option key={relation.relation_id} value={relation.patient_id}>{relation.display_id}</option>)}</select></label>
        : <button className="text-action" onClick={() => navigate(noRelations ? '/associations/new' : access.principalId ? '/associations' : '/login')}>{noRelations ? '添加患儿关联' : '核验患儿关联'}</button>}>
      {patientSelectionError && <UI2ReadState requirePatient={false} error={patientSelectionError} onRetry={retryPatientSelection} />}
      {observationError && <UI2ReadState error={observationError} onRetry={() => setReload(value => value + 1)} />}
    </HomeCompanion>
    <HomeQuickActions onObserve={() => navigate('/register/observations')} onReports={() => navigate('/register/reports')} onTherapies={() => navigate('/register/therapies')} />
    <div className="home-secondary-tools">
      <button onClick={() => navigate('/archives')}><Layers size={20} />整理长期档案<ArrowRight size={14} /></button>
      <button onClick={() => navigate('/records/period')}>查看观察时段<ArrowRight size={14} /></button>
    </div>
  </UI2FamilyFrame>;
}
