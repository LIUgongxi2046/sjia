import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError } from '../../api/client';
import { readPurposeOptions } from '../../api/purpose';
import type { Purpose, PurposeCopyInput, PurposeCopyOptionsView } from '../../../../frontend-design/v2-types';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';

export function PurposeCopyCreation({ fileId, purpose, active, pending, submitted, error, onSubmit, onPermission, onRead }: {
  fileId: string; purpose: Purpose; active: boolean; pending: boolean; submitted: boolean; error: string | null;
  onSubmit: (payload: PurposeCopyInput) => Promise<void>; onPermission: () => void;
  onRead: (options: PurposeCopyOptionsView | null) => void;
}) {
  const [options, setOptions] = useState<PurposeCopyOptionsView | null>(null);
  const [state, setState] = useState<SupportReadState>('loading');
  const [readError, setReadError] = useState<string | null>(null);
  const [authorizationId, setAuthorizationId] = useState('');
  const [configId, setConfigId] = useState('');
  const controller = useRef<AbortController>();
  const composing = useRef(false);
  const refresh = useCallback(async () => {
    controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal;
    setOptions(null); setState('loading'); setReadError(null); setAuthorizationId(''); setConfigId('');
    onRead(null);
    if (!active) return;
    try {
      const result = await readPurposeOptions(fileId, purpose, signal);
      if (result.original_file_id !== fileId || result.purpose !== purpose) throw new Error('当前原件与用途不一致，请重新选择资料。');
      if (signal.aborted) return;
      setOptions(result); onRead(result); setState('default');
    } catch (failure) {
      if (signal.aborted) return;
      const denied = failure instanceof ApiError && [401, 403].includes(failure.status);
      setState(denied ? 'permission' : navigator.onLine ? 'error' : 'offline');
      setReadError(failure instanceof Error ? failure.message : '用途范围读取失败。');
      if (denied) onPermission();
    }
  }, [active, fileId, purpose, onPermission, onRead]);
  useEffect(() => { void refresh(); return () => controller.current?.abort(); }, [refresh]);
  const authorization = options?.authorizations.find(item => item.authorization_id === authorizationId);
  const config = options?.processing_configs.find(item => item.processing_config_id === configId);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || !options || !authorization || !config || pending || submitted || composing.current) return;
    await onSubmit({ purpose: options.purpose, target: config.target,
      authorization_id: authorization.authorization_id, processing_config_id: config.processing_config_id });
  }
  return <>
    <SupportReadStatus state={active ? state : 'loading'} error={readError} onRefresh={() => void refresh()} />
    {active && state === 'default' && options && <form className="card form-card" onSubmit={event => void submit(event)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <h3>生成用途副本</h3>
      <div className="pair"><span>目标环境</span><strong>{options.environment}</strong></div>
      <label className="field"><span>用途授权</span><select required value={authorizationId} disabled={pending || submitted}
        onChange={event => setAuthorizationId(event.target.value)}>
        <option value="" disabled>请选择</option>
        {options.authorizations.map(item => <option key={item.authorization_id} value={item.authorization_id}>{item.evidence_ref} · {item.approved_at}</option>)}
      </select></label>
      <label className="field"><span>批准处理方案</span><select required value={configId} disabled={pending || submitted}
        onChange={event => setConfigId(event.target.value)}>
        <option value="" disabled>请选择</option>
        {options.processing_configs.map(item => <option key={item.processing_config_id} value={item.processing_config_id}>原件第 {item.source_page_number} 页 · {item.target} · 版本 {item.version}</option>)}
      </select></label>
      {config && <>
        <div className="pair"><span>准确原页</span><strong>原件第 {config.source_page_number} 页；{config.file_page_id}</strong></div>
        <div className="pair"><span>日期处理</span><strong>{config.date_strategy === 'unchanged' ? '保持原有日期' : '按批准方案一致调整'}</strong></div>
        <div className="pair"><span>处理批准证明</span><strong>{config.approval_evidence_refs.join('；')}</strong></div>
        <div className="pair"><span>处理验证证明</span><strong>{config.validation_evidence_refs.join('；')}</strong></div>
      </>}
      {(!options.authorizations.length || !options.processing_configs.length) && <p role="status">当前原件和用途缺少有效授权或批准处理方案。</p>}
      {error && <p role="alert">{error}</p>}
      <button className="button" disabled={!authorization || !config || pending || submitted} type="submit">
        {submitted ? '处理任务已经登记' : pending ? '保存结果待确认' : '确认生成副本'}
      </button>
    </form>}
  </>;
}
