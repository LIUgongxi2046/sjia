import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router';
import { Check, ChevronRight } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { useAccess } from '../../state/access';
import logo from '../../../../ui-delivery/identity/2x/logo-embrace@2x.png';

export function LoginPage() {
  const { login, register, logout, refresh, principalId, accountStatus, patientId, relations, error: accessError, errorSource, loading } = useAccess();
  const navigate = useNavigate();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const loggedIn = !!principalId && accountStatus === 'active';
  const relation = relations.find(value => value.patient_id === patientId);
  const actions = relation?.actions.map(action => ({ read_raw: '查看资料', write_record: '登记资料',
    correct_record: '更正记录', receive_alert: '接收提示' } as Record<string, string>)[action]).filter(Boolean) ?? [];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    const submittedPassword = password;
    setPassword(''); setError(null); setSubmitting(true);
    try {
      const ready = mode === 'login' ? await login(username, submittedPassword) : await register(username, submittedPassword);
      if (ready) navigate('/home', { replace: true });
    } catch (error) {
      setError(error instanceof Error ? error.message : '暂时无法完成身份核验，请重试。');
    } finally { submittingRef.current = false; setPassword(''); setSubmitting(false); }
  }

  return <UI2FamilyFrame title={loggedIn ? "登录身份" : mode === "register" ? "注册账号" : "登录"} screenId="UI2-17" note="使用账号和密码完成身份核验。登录后仍需取得患儿关联与相应授权，才能访问患儿资料。" backTo="/account"
    kicker={!loggedIn && !accessError ? null : undefined} subtitle={!loggedIn && !accessError ? null : undefined}>
    <div className="login-brand"><img src={logo} alt="sJIA 守护者联盟标志" /><h2>sJIA 守护者联盟</h2><p>儿童健康记录</p></div>
    {accessError ? <section className="card form-card" role="alert"><h3>{errorSource === 'relations' ? '当前账号已登录' : '账号会话暂未核验'}</h3>
      <p>{errorSource === 'relations' ? '患儿关联暂时无法读取。账号已建立，无需重复注册；关联核验成功前不能访问患儿资料。' : '暂时无法确认当前会话，请重新核验后继续。'}</p>
      <p className="muted">{accessError}</p>
      <button className="button" disabled={loading} onClick={() => void refresh()}>重新核验访问权限</button>
      {loggedIn && <button className="button secondary" disabled={loading} onClick={() => void logout().catch(() => { /* Provider presents the failure. */ })}>退出登录</button>}
    </section> : loggedIn ? <><section className="login-ready"><span className="login-success-icon"><Check size={36} /></span><h2>当前账号已登录</h2><p>{relation ? '可以访问本人获准的患儿资料。' : '当前尚无可访问的患儿资料，请先核验患儿关联。'}</p></section><section className="card form-card">
      <div className="pair"><span>当前患儿</span><strong>{relation?.display_id ?? '尚未选择患儿'}</strong></div>
      <div className="pair"><span>关联状态</span><strong>{relation ? '有效关联' : '尚无有效关联'}</strong></div>
      <div className="pair"><span>访问范围</span><strong>{actions.join('、') || '尚无患儿访问权限'}</strong></div>
    </section><button className="button" onClick={() => navigate('/home')}>进入首页</button>
      <button className="button secondary" onClick={() => navigate('/associations')}>管理患儿关联</button>
    </> : <form className="card form-card" aria-label={mode === 'login' ? '登录' : '注册账号'} onSubmit={event => void submit(event)} aria-busy={submitting}>
      <h3>{mode === 'login' ? '登录' : '注册账号'}</h3>
      <label className="field"><span>账号</span><input name="username" placeholder="账号" required disabled={submitting} value={username}
        onChange={event => setUsername(event.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} aria-describedby={error ? 'login-error' : undefined} /></label>
      <label className="field"><span>密码</span><input name="password" placeholder={mode === 'login' ? '密码' : '密码（至少 8 位）'} type="password"
        required minLength={mode === 'register' ? 8 : undefined} disabled={submitting} value={password} onChange={event => setPassword(event.target.value)}
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'} aria-describedby={error ? 'login-error' : undefined} /></label>
      {error && <div className="status-panel error" id="login-error" role="alert"><h2>身份核验未完成</h2><p>{error}</p></div>}
      <button type="submit" className="button" disabled={submitting}>{submitting ? '正在核验身份' : mode === 'login' ? '登录' : '注册并登录'}</button>
      <button type="button" className="button secondary" disabled={submitting} onClick={() => {
        setMode(mode === 'login' ? 'register' : 'login'); setPassword(''); setError(null);
      }}>{mode === 'login' ? '没有账号，去注册' : '已有账号，去登录'}</button>
    </form>}
    <button className="text-action center" onClick={() => navigate('/account/data-use')}>了解资料如何使用<ChevronRight size={15} /></button>
  </UI2FamilyFrame>;
}
