import { useEffect, useRef, useState } from 'react';
import { Check, Keyboard, Mic, RotateCcw, Square, X } from './ThreeDIcon';
import { readVoiceInputOptions, type VoiceInputOptions } from '../api/voice';
import { useAccess } from '../state/access';

type Phase = 'idle' | 'starting' | 'listening' | 'transcribing' | 'review' | 'error';
const microphoneErrors: Record<string, string> = {
  NotAllowedError: '麦克风权限未开启，请在浏览器设置中允许使用后重试。',
  NotFoundError: '没有可用的麦克风，请检查设备连接。',
  NotReadableError: '麦克风当前无法使用，请检查系统权限或其他录音程序。',
};

export interface VoiceTranscriptSource {
  inputMode: 'LOCAL_AUDIO_CONFIRMED_TEXT' | 'SIMULATION_TEXT_ONLY';
  transcriptionEvidenceId: string | null;
  transcriptionAuthorizationId: string | null;
}

export function VoiceEntry({ onApply, onBusyChange, structured = false, ui2 = false }: { onApply: (text: string, source?: VoiceTranscriptSource) => void; onBusyChange: (busy: boolean) => void; structured?: boolean; ui2?: boolean }) {
  const access = useAccess();
  const { patientId, csrfToken, principalId, scopeVersion } = access;
  const relation = access.relations.find(value => value.patient_id === patientId);
  const canWrite = Boolean(principalId && patientId && csrfToken && !access.loading && !access.error
    && relation?.actions.includes('write_record') && access.hasCurrentAccess(principalId, patientId, scopeVersion));
  const scopeKey = `${principalId}:${patientId}:${scopeVersion}`;
  const hasAccess = () => canWrite && access.hasCurrentAccess(principalId, patientId, scopeVersion);
  const [phase, setPhase] = useState<Phase>('idle');
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [applied, setApplied] = useState(false);
  const [retryAudio, setRetryAudio] = useState<Blob | null>(null);
  const [options, setOptions] = useState<VoiceInputOptions | null>(null);
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [transcriptionEvidenceId, setTranscriptionEvidenceId] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const candidate = useRef('');
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const active = phase === 'starting' || phase === 'listening' || phase === 'transcribing';
  const canRecord = window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== 'undefined';
  const selectedSource = options?.source_options.find(option => option.source_revision_id === selectedSourceId);
  const audioAllowed = Boolean(selectedSource && options?.input_modes.includes('LOCAL_AUDIO_CONFIRMED_TEXT'));

  useEffect(() => {
    if (!patientId || !canWrite) { setOptions(null); return; }
    const request = new AbortController();
    setOptions(null);
    const isCurrent = () => !request.signal.aborted && hasAccess();
    void readVoiceInputOptions(patientId, request.signal, isCurrent).then(value => {
      if (!isCurrent()) return;
      if (value.patient_id !== patientId) throw new Error('语音选项与当前患儿不一致');
      setOptions(value);
      setSelectedSourceId(current => value.source_options.some(source => source.source_revision_id === current) ? current : '');
    }).catch(failure => { if (isCurrent()) setError(failure instanceof Error ? failure.message : '语音选项无法读取'); });
    return () => request.abort();
  }, [scopeKey, canWrite, structured]);

  function releaseMicrophone() {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
  }

  function stop() {
    if (!hasAccess()) { invalidate(); return; }
    if (recorder.current?.state === 'recording') {
      recorder.current.stop();
      setPhase('transcribing');
      releaseMicrophone();
    }
  }

  function invalidate() {
    generation.current++;
    controller.current?.abort();
    controller.current = null;
    const current = recorder.current;
    recorder.current = null;
    if (current && current.state !== 'inactive') current.stop();
    releaseMicrophone();
  }

  const previousScope = useRef(scopeKey);
  useEffect(() => {
    if (previousScope.current !== scopeKey) {
      previousScope.current = scopeKey; candidate.current = ''; setText(''); setSelectedSourceId('');
      setTranscriptionEvidenceId(null); setApplied(false); setExpanded(false);
    }
    if (!canWrite) {
      const interrupted = recorder.current !== null || controller.current !== null;
      invalidate(); setRetryAudio(null); setPhase(candidate.current ? 'review' : 'idle');
      if (interrupted) setError('访问核验已暂停，本次转写结果尚未确认；不会自动重新发送录音。');
    }
    return () => { invalidate(); };
  }, [scopeKey, canWrite]);
  useEffect(() => { onBusyChange(canWrite && (active || Boolean(text.trim()))); }, [active, text, canWrite, onBusyChange]);
  useEffect(() => {
    const hidden = () => { if (document.hidden) stop(); };
    const unload = (event: BeforeUnloadEvent) => {
      if (recorder.current || controller.current || candidate.current.trim()) { event.preventDefault(); event.returnValue = ''; }
    };
    document.addEventListener('visibilitychange', hidden);
    window.addEventListener('beforeunload', unload);
    return () => {
      document.removeEventListener('visibilitychange', hidden);
      window.removeEventListener('beforeunload', unload);
      invalidate();
    };
  }, [scopeKey, canWrite]);

  async function transcribe(audio: Blob, token: number) {
    if (token !== generation.current || !hasAccess()) return;
    if (!patientId || !selectedSource || !audioAllowed) {
      setPhase('error'); setError('请先选择已批准的语音用途授权来源。'); return;
    }
    setPhase('transcribing'); setError(''); setRetryAudio(null);
    const request = new AbortController();
    controller.current = request;
    const timeout = setTimeout(() => request.abort(new Error('转写超过两分钟，请重新尝试。')), 120000);
    const headers: Record<string, string> = { Accept: 'application/json', 'Content-Type': audio.type,
      'X-Use-Authorization': selectedSource.family_authorization_id };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    await fetch(`/api/patients/${patientId}/voice-transcription`, { method: 'POST', body: audio, headers,
      signal: request.signal, credentials: 'same-origin' }).then(async response => {
      if (token !== generation.current || !hasAccess() || request.signal.aborted) throw new DOMException('访问核验已暂停。', 'AbortError');
      if (!response.ok) {
        const body = await response.json() as { message?: string; code?: string };
        throw new Error(body.message ?? body.code ?? `语音转写请求失败（${response.status}）`);
      }
      return await response.json() as { text: string; transcription_evidence_id: string };
    }).then(result => {
      if (token !== generation.current || !hasAccess() || request.signal.aborted) return;
      candidate.current = structured ? result.text : [candidate.current, result.text].filter(Boolean).join('\n');
      setTranscriptionEvidenceId(result.transcription_evidence_id);
      setText(candidate.current); setPhase('review');
    }, failure => {
      if (token !== generation.current || !hasAccess()) return;
      setError(request.signal.aborted ? '语音转写超过两分钟，请重新尝试。' : failure instanceof Error ? failure.message : '无法连接语音转写服务，请检查网络后重新尝试。');
      setRetryAudio(audio); setPhase('error');
    }).finally(() => { clearTimeout(timeout); if (controller.current === request) controller.current = null; });
  }

  async function start() {
    if (!canRecord || !patientId || !audioAllowed || active || !hasAccess()) return;
    const token = ++generation.current;
    setExpanded(true); setError(''); setApplied(false); setRetryAudio(null); setSeconds(0); setPhase('starting');
    await navigator.mediaDevices.getUserMedia({ audio: true }).then(media => {
      if (token !== generation.current || !hasAccess()) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) { releaseMicrophone(); throw new Error('浏览器不支持当前录音格式，请选择已有录音。'); }
      const engine = new MediaRecorder(media, { mimeType });
      const chunks: Blob[] = [];
      recorder.current = engine;
      engine.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      engine.onstop = () => {
        if (token !== generation.current || !hasAccess()) return;
        recorder.current = null;
        releaseMicrophone();
        void transcribe(new Blob(chunks, { type: mimeType }), token);
      };
      engine.onerror = () => {
        if (token !== generation.current || !hasAccess()) return;
        invalidate(); setPhase('error'); setError('录音没有完成，请检查麦克风后重试。');
      };
      engine.start();
      setPhase('listening');
      const started = Date.now();
      timer.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - started) / 1000);
        setSeconds(elapsed);
        if (elapsed >= 60) stop();
      }, 250);
    }).catch(failure => {
      if (token !== generation.current || !hasAccess()) return;
      releaseMicrophone(); setPhase('error');
      setError(microphoneErrors[failure.name] || failure.message);
    });
  }

  function cancel() {
    const wasActive = active;
    invalidate(); setRetryAudio(null); setError(''); setApplied(false);
    if (!wasActive) { candidate.current = ''; setText(''); }
    setPhase(candidate.current ? 'review' : 'idle');
  }

  function apply() {
    const value = candidate.current.trim();
    if (active || !value || !hasAccess()) return;
    const inputMode = transcriptionEvidenceId ? 'LOCAL_AUDIO_CONFIRMED_TEXT'
      : options?.input_modes.includes('SIMULATION_TEXT_ONLY') ? 'SIMULATION_TEXT_ONLY' : null;
    if (structured && (!selectedSource || !inputMode)) { setError('当前没有可用的模拟文字或获准来源，请先读取语音选项。'); return; }
    onApply(value, structured && inputMode ? { inputMode, transcriptionEvidenceId,
      transcriptionAuthorizationId: transcriptionEvidenceId ? selectedSource?.family_authorization_id ?? null : null } : undefined);
    candidate.current = ''; setText(''); setRetryAudio(null); setExpanded(false); setPhase('idle'); setApplied(true);
  }

  return <section className={ui2 ? 'card form-card' : `voice-entry ${active ? 'voice-active' : ''}`} aria-label="症状语音输入">
    <div className={ui2 ? 'section-heading' : 'voice-heading'}><span className="voice-icon"><Mic size={23} /></span><div><h2>语音记录症状</h2><p>描述发生时间、体温和观察到的表现</p></div></div>
    <div className={ui2 ? 'two-buttons' : 'voice-actions'}>
      {active ? <>
        <button type="button" className={ui2 ? 'button' : 'action'} onClick={stop} disabled={phase !== 'listening'}><Square size={17} />{phase === 'starting' ? '等待麦克风授权…' : phase === 'transcribing' ? '正在转写…' : '结束并核对'}</button>
        <button type="button" className={ui2 ? 'button secondary' : 'voice-cancel'} onClick={cancel}><X size={17} />取消本次</button>
      </> : <>
        <button type="button" className={ui2 ? 'button' : 'action'} onClick={() => void start()} disabled={!canWrite || !canRecord || !patientId || !audioAllowed}><Mic size={19} />{text ? '继续说话' : '开始语音输入'}</button>
        <button type="button" className={ui2 ? 'button secondary' : 'voice-keyboard'} onClick={() => { setExpanded(true); setError(''); setApplied(false); }} disabled={!canWrite || structured && (!selectedSource || !options?.input_modes.includes('SIMULATION_TEXT_ONLY'))}><Keyboard size={19} />{structured ? 'SIMULATION_TEXT_ONLY 文字输入' : '文字输入'}</button>
      </>}
    </div>
    {<label className="field"><span>语音用途授权来源</span><select aria-label="语音用途授权来源" value={selectedSourceId}
      disabled={!canWrite || active || !options || options.state !== 'available'} onChange={event => setSelectedSourceId(event.target.value)}>
      <option value="">请选择已批准的来源</option>{options?.source_options.map(source => <option key={source.source_revision_id} value={source.source_revision_id}>来源版本 {source.source_revision_id}</option>)}
    </select>{options?.state === 'unavailable' && <small>{options.reason_code ?? '当前没有可用的语音用途授权。'}</small>}</label>}
    {structured && !transcriptionEvidenceId && options?.input_modes.includes('SIMULATION_TEXT_ONLY')
      && <p role="status">当前文字输入按 SIMULATION_TEXT_ONLY 保存。</p>}
    {active && <p className="voice-status" role="status">{phase === 'listening' ? `正在录音 · ${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')} / 01:00` : phase === 'starting' ? '请允许本页面使用麦克风。' : '录音已经结束，正在转写文字。'}</p>}
    {!canRecord && <p className="voice-status">当前浏览器无法录音，可以选择录音文件或输入文字。</p>}
    <input ref={audioInput} type="file" accept="audio/webm,audio/mp4,audio/mpeg,audio/wav,audio/x-wav,audio/ogg" hidden aria-label="选择语音录音" onChange={event => {
      const file = event.target.files?.[0]; event.target.value = '';
      if (!file || active || !hasAccess()) return;
      setExpanded(true); setApplied(false);
      if (!file.size || file.size > 5 * 1024 * 1024) { setError('录音不能为空，且不能超过5MB。'); return; }
      void transcribe(file, ++generation.current);
    }} />
    <button className={ui2 ? 'text-action' : 'text-link'} type="button" disabled={!canWrite || active || !patientId || !audioAllowed} onClick={() => audioInput.current?.click()}>选择已有录音（60秒以内）</button>
    {error && <p role="alert" className="voice-error">{error}</p>}
    {retryAudio && !active && <button className={ui2 ? 'button secondary' : 'action secondary'} type="button" disabled={!canWrite} onClick={() => void transcribe(retryAudio, ++generation.current)}>重新转写本次录音</button>}
    {expanded && <div className={ui2 ? 'form-card' : 'voice-review'}>
      <label className="field"><span>核对本次描述</span><textarea aria-label="语音转写与文字描述" readOnly={active || !canWrite} value={text} placeholder="本次观察到的表现，以及发生时间" onChange={event => { candidate.current = event.target.value; setText(event.target.value); setPhase('review'); setApplied(false); }} /></label>
      <p>核对体温数字、时间和“有／没有”等表达后加入说明。</p>
      <button type="button" className={ui2 ? 'button secondary' : 'action secondary'} disabled={!canWrite || active || !text.trim()} onClick={apply}><Check size={18} />{structured ? '提取候选并核对' : '确认加入症状说明'}</button>
      {!active && text && <button type="button" className={ui2 ? 'text-action' : 'text-link'} onClick={cancel}><RotateCcw size={16} />清空本次描述</button>}
    </div>}
    {applied && <p role="status" className="voice-status">{structured ? '请在候选区域逐项核对后确认采用。' : '已加入症状说明，请核对其他字段后保存记录。'}</p>}
    <details className="voice-privacy"><summary>录音由本项目服务转写 · 查看说明</summary><p>声音发送至本项目服务，在内存中完成转写，不保存录音文件。{structured ? '核对文字后，通过获准的 Ark 服务提取候选，逐项确认后才加入本次记录。' : '确认后的文字加入本次说明，体温与症状字段由本人核对填写。'}</p></details>
  </section>;
}
