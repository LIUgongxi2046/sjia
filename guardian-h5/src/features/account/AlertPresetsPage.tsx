import { Check, CircleAlert, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { useAccess } from '../../state/access';
import { alertPresetAccessKey, useAlertPresetSelection } from '../../state/useAlertPresetSelection';

export function AlertPresetsPage() {
  const access = useAccess();
  return <AlertPresetsContent key={alertPresetAccessKey(access)} />;
}
function AlertPresetsContent() {
  const state = useAlertPresetSelection();
  const { data, original } = state;
  const superseded = original?.state === 'failed' && !original.may_retry && original.error_code === 'PRESET_SELECTION_SUPERSEDED';
  const retryable = original && (original.may_retry || original.state === 'pending' && Date.parse(original.expires_at) > Date.now());
  return <UI2FamilyFrame title="预警方案" screenId="UI2-ALERT-PRESETS" note="专业人员维护并审批预警参数，家属仅能选择当前患儿已批准的方案。" backTo="/account">
    <section className="privacy-intro"><ShieldCheck size={34} aria-hidden="true" /><h2>选择已批准的方案</h2>
      <p>方案由专业人员配置并审核。请结合专业人员的说明选择，医学阈值与权重由专业人员维护。</p></section>
    <UI2ReadState loading={state.access.loading || state.canAccess && state.loading} error={state.readError} onRetry={state.refresh} />
    {!state.access.loading && !state.access.error && state.access.principalId && state.access.patientId && !state.canAccess &&
      <section className="status-panel permission" role="status"><h2>当前没有方案选择权限</h2><p>患儿关联或授权已到期、尚未核验，或未包含方案选择权限。重新核验后才能操作。</p>
        <button className="text-action" onClick={() => void state.access.refresh()}>重新核验访问权限</button></section>}
    {state.canAccess && state.readError && <section className="note"><p>当前未取得可用的已批准方案。请重新读取；若仍不可用，请联系专业人员核对方案审批与有效期。</p></section>}
    {data && <><section className="card form-card"><div className="section-heading"><h2>{data.display_name}</h2><span className="badge blue">专业审核</span></div>
      <p className="footnote">{data.selection_state === 'professional_default' ? '当前采用专业人员指定的默认方案。' : data.selection_state === 'selected' ? '当前已保存你的方案选择。' : '原方案或授权已变更，请重新选择当前已批准的方案。'}</p>
      {data.options.length === 0 ? <div className="empty" role="status"><h3>暂无可选方案</h3><p>专业人员完成方案审批后，才能在这里选择。</p></div> :
        <fieldset disabled={!state.canAccess || state.locked || state.busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <legend className="eyebrow" style={{ marginBottom: 12 }}>可选预警方案</legend>
          {data.options.map(option => <label key={option.schedule_config_id} className="checkbox-row" style={{ alignItems: 'flex-start', padding: '15px 0', borderTop: '1px solid var(--line)', cursor: state.locked ? 'default' : 'pointer' }}>
            <input type="radio" name="approved-alert-preset" value={option.schedule_config_id} checked={state.selection === option.schedule_config_id}
              onChange={() => state.select(option.schedule_config_id)} aria-describedby={`preset-description-${option.schedule_config_id}`} style={{ flexShrink: 0, marginTop: 3 }} />
            <span style={{ display: 'grid', gap: 7 }}><strong>{option.label}</strong><span id={`preset-description-${option.schedule_config_id}`} style={{ color: 'var(--muted)', lineHeight: 1.75 }}>{option.description}</span>
              {data.selected_schedule_config_id === option.schedule_config_id && <span className="badge blue" style={{ justifySelf: 'start' }}>{data.selection_state === 'professional_default' ? '专业默认' : '当前方案'}</span>}</span>
          </label>)}
        </fieldset>}
    </section><button className="button" disabled={!state.canSave} onClick={() => void state.choose()}><SlidersHorizontal size={20} aria-hidden="true" />{state.busy ? '正在核对本次选择' : '保存方案选择'}</button></>}
    {state.canAccess && state.locked && <section className="status-panel pending" role="status"><div className="status-top"><CircleAlert size={22} /><span>原请求待核对</span></div>
      <h2>{state.busy ? '正在核对本次选择' : superseded ? '原选择已失效且确认未保存' : original?.may_retry ? '原请求已过期或需要重试' : '尚未确认保存结果'}</h2>
      <p>本次选择已锁定。重新打开本页会查询原请求，确认结果前请勿另行提交。</p>
      {original?.state === 'unknown' && <p>服务端结果仍未明确，请稍后继续查询。</p>}
      <button className="text-action" disabled={state.busy} onClick={() => void state.recover(false)}>核对原请求</button>
      {(!original || retryable) && <button className="button secondary" disabled={state.busy || !state.access.csrfToken} onClick={() => void state.recover(true)}>重试本次选择</button>}
      {superseded && <button className="text-action" disabled={state.busy} onClick={() => void state.releaseUncommitted()}>确认未保存后重新选择</button>}
    </section>}
    {state.canAccess && state.error && <section className="status-panel error" role="alert"><h2>方案选择暂未完成</h2><p>{state.error}</p>{state.locked && <p>已保留原请求，请先核对保存结果。</p>}</section>}
    {state.canAccess && state.saved && <section className="status-panel success" role="status"><div className="status-top"><Check size={22} /><span>选择已保存</span></div><h2>已保存方案选择</h2><p>方案选择已登记，当前可用状态以重新读取结果为准。这里的选择不替代专业诊疗意见。</p></section>}
    <section className="note"><p>预警提示用于家庭观察，不能据此自行调整治疗。如有不适或病情变化，请及时联系专业人员。</p></section>
  </UI2FamilyFrame>;
}
