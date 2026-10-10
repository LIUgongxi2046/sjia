import React, {useEffect,useRef,useState} from 'react';
import {Mic,Square,Keyboard,Check,RotateCcw,X} from './three-d-icons.jsx';
import './voice-entry.css';

const messages={
  'not-allowed':'麦克风权限未开启，请在浏览器设置中允许使用后重试。',
  'service-not-allowed':'浏览器当前不允许使用语音识别服务。',
  'audio-capture':'没有可用的麦克风，请检查设备连接与系统权限。',
  'no-speech':'没有识别到语音，可以重新说一遍。',
  network:'语音服务连接失败，已取得的文字仍可核对。',
  'language-not-supported':'当前语音服务不支持普通话。',
  aborted:'语音输入已停止。',
};

export function VoiceEntry({onApply,onBusyChange}) {
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  const [phase,setPhase]=useState('idle');
  const [text,setText]=useState('');
  const [interim,setInterim]=useState('');
  const [error,setError]=useState('');
  const [expanded,setExpanded]=useState(false);
  const [seconds,setSeconds]=useState(0);
  const [applied,setApplied]=useState(false);
  const session=useRef(null),candidate=useRef(''),prefix=useRef(''),failure=useRef(false),discard=useRef(false),timer=useRef(null),watchdog=useRef(null),started=useRef(0);
  const active=['starting','listening','stopping'].includes(phase);
  function clearTimers(){clearInterval(timer.current);clearTimeout(watchdog.current);}
  useEffect(()=>{onBusyChange(active||Boolean(text.trim()));},[active,text,onBusyChange]);
  useEffect(()=>{
    const leave=event=>{
      if(session.current){event.preventDefault();setError('请结束或取消当前语音输入后返回。');return;}
      if(candidate.current.trim()&&!window.confirm('这段文字尚未加入症状说明，确认离开？'))event.preventDefault();
    };
    const hidden=()=>{if(document.hidden&&session.current){setPhase('stopping');session.current.stop();}};
    const unload=event=>{if(session.current||candidate.current.trim()){event.preventDefault();event.returnValue='';}};
    window.addEventListener('document:navigate',leave);
    window.addEventListener('beforeunload',unload);
    document.addEventListener('visibilitychange',hidden);
    return ()=>{
      window.removeEventListener('document:navigate',leave);window.removeEventListener('beforeunload',unload);document.removeEventListener('visibilitychange',hidden);clearTimers();
      if(session.current){const engine=session.current;session.current=null;engine.onstart=null;engine.onresult=null;engine.onerror=null;engine.onend=null;engine.abort();}
    };
  },[]);
  function start(){
    if(!Recognition||!window.isSecureContext){setExpanded(true);setError('当前浏览器无法使用语音输入，可以选择文字输入。');return;}
    if(session.current)return;
    const engine=new Recognition();
    engine.lang='zh-CN';engine.continuous=true;engine.interimResults=true;engine.maxAlternatives=1;
    session.current=engine;prefix.current=candidate.current;failure.current=false;discard.current=false;
    setExpanded(true);setError('');setInterim('');setSeconds(0);setApplied(false);setPhase('starting');
    engine.onstart=()=>{
      if(session.current!==engine)return;
      clearTimeout(watchdog.current);setPhase('listening');started.current=Date.now();
      timer.current=setInterval(()=>{const duration=Math.floor((Date.now()-started.current)/1000);setSeconds(duration);if(duration>=60){clearTimers();setPhase('stopping');engine.stop();}},250);
    };
    engine.onresult=event=>{
      if(session.current!==engine||discard.current)return;
      let finalized='',pending='';
      for(let index=0;index<event.results.length;index++){
        const result=event.results[index];
        if(result.isFinal)finalized+=result[0].transcript;else pending+=result[0].transcript;
      }
      candidate.current=[prefix.current,finalized].filter(Boolean).join('\n');setText(candidate.current);setInterim(pending);
    };
    engine.onerror=event=>{
      if(session.current!==engine||discard.current)return;
      failure.current=true;setError(messages[event.error]||`语音识别未完成（${event.error}），请重新输入。`);setInterim('');clearTimers();setPhase('error');
    };
    engine.onend=()=>{
      if(session.current!==engine)return;
      session.current=null;clearTimers();setInterim('');
      if(discard.current){candidate.current=prefix.current;setText(prefix.current);setPhase(prefix.current?'review':'idle');return;}
      if(failure.current){setPhase('error');return;}
      setPhase(candidate.current.trim()?'review':'idle');
      if(!candidate.current.trim())setError('没有取得可确认的文字，请重新输入。');
    };
    watchdog.current=setTimeout(()=>{
      if(session.current!==engine)return;
      failure.current=true;setError('语音服务尚未响应，请稍后重新输入。');setPhase('error');engine.abort();
    },15000);
    engine.start();
  }
  function stop(){if(!session.current)return;clearTimers();setPhase('stopping');session.current.stop();}
  function cancel(){if(session.current){discard.current=true;session.current.abort();}else{candidate.current='';setText('');setPhase('idle');}setInterim('');setError('');setApplied(false);}
  function apply(){const value=candidate.current.trim();if(active||!value)return;onApply(value);candidate.current='';prefix.current='';setText('');setInterim('');setExpanded(false);setPhase('idle');setApplied(true);}
  return <section className={`voice-entry ${active?'voice-active':''}`} aria-label="症状语音输入">
    <div className="voice-heading"><span className="voice-icon"><Mic size={23}/></span><div><h2>语音记录症状</h2><p>描述发生时间、体温和观察到的表现</p></div></div>
    <div className="voice-actions">{active?<><button type="button" className="action" onClick={stop} disabled={phase!=='listening'}><Square size={17}/>{phase==='starting'?'正在连接…':phase==='stopping'?'正在完成转写…':'结束并核对'}</button><button type="button" className="voice-cancel" onClick={cancel}><X size={17}/>取消本次</button></>:<><button type="button" className="action" onClick={start} disabled={!Recognition||!window.isSecureContext}><Mic size={19}/>{text?'继续说话':'开始语音输入'}</button><button type="button" className="voice-keyboard" onClick={()=>{setExpanded(true);setError('');setApplied(false);}}><Keyboard size={19}/>文字输入</button></>}</div>
    {active&&<p className="voice-status" role="status">{phase==='listening'?`正在聆听 · ${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')} / 01:00`:'请稍候，正在处理当前语音。'}</p>}
    {!Recognition&&<p className="voice-status">当前浏览器不支持语音识别，文字输入可以正常使用。</p>}
    {error&&<p role="alert" className="voice-error">{error}</p>}
    {expanded&&<div className="voice-review"><label className="form-field"><span>{active?'已转写的文字':'核对本次描述'}</span><textarea aria-label="语音转写与文字描述" readOnly={active} value={text} placeholder="本次观察到的表现，以及发生时间" onChange={event=>{candidate.current=event.target.value;setText(event.target.value);setPhase('review');setApplied(false);}}/></label>{interim&&<p className="voice-interim" aria-live="polite">正在转写：{interim}</p>}<p>核对体温数字、时间和“有／没有”等表达后加入说明。</p><button type="button" className="action secondary" disabled={active||!text.trim()} onClick={apply}><Check size={18}/>确认加入症状说明</button>{!active&&text&&<button type="button" className="text-link" onClick={cancel}><RotateCcw size={16}/>清空本次描述</button>}</div>}
    {applied&&<p role="status" className="voice-status">已加入症状说明，请核对其他字段后保存记录。</p>}
    <details className="voice-privacy"><summary>通过浏览器在线语音服务转写 · 查看说明</summary><p>开始后，声音可能发送至浏览器提供的在线识别服务。本页面不保存录音；确认后的文字加入本次说明，体温与症状字段由本人核对填写。</p></details>
  </section>;
}
