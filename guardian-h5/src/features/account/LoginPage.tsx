import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight, ShieldCheck } from 'lucide-react';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { useAccess } from '../../state/access';
import logo from '../../../../ui-delivery/identity/2x/logo-embrace@2x.png';

export function LoginPage() {
  const { login, register, principalId, accountStatus, patientId, relations } = useAccess();
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
    setError(null); setSubmitting(true);
    try {
      if (mode === 'login') await login(username, password);
      else await register(username, password);
      setPassword('');
      navigate('/home', { replace: true });
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      setError(error.message);
    } finally { submittingRef.current = false; setSubmitting(false); }
  }

  return <UI2FamilyFrame title="登录" screenId="UI2-17" note="明确身份方式尚需配置，不能伪造登录成功。" backTo="/account">
    <div className="login-brand"><img src={logo} alt="sJIA 守护者联盟标志" /><h2>sJIA 守护者联盟</h2><p>为孩子，保存每次重要的记录。</p></div>
    {loggedIn ? <section className="card form-card"><h3>当前账号已登录</h3><p className="muted">可以访问本人获准的患儿资料。</p>
      <div className="pair"><span>当前患儿</span><strong>{relation?.display_id ?? '尚未选择患儿'}</strong></div>
      <div className="pair"><span>关联状态</span><strong>{relation ? '有效关联' : '尚无有效关联'}</strong></div>
      <div className="pair"><span>访问范围</span><strong>{actions.join('、') || '尚无患儿访问权限'}</strong></div>
      <button className="button" onClick={() => navigate('/home')}>进入首页</button>
      <button className="button secondary" onClick={() => navigate('/associations')}>管理患儿关联</button>
    </section> : <form className="card form-card" aria-label={mode === 'login' ? '登录账号' : '建立账号'} onSubmit={event => void submit(event)} aria-busy={submitting}>
      <h3>{mode === 'login' ? '登录后访问本人获准资料' : '建立本人账号'}</h3>
      <label className="field"><span>账号</span><input name="username" placeholder="账号" required disabled={submitting} value={username}
        onChange={event => setUsername(event.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} aria-describedby={error ? 'login-error' : undefined} /></label>
      <label className="field"><span>密码</span><input name="password" placeholder={mode === 'login' ? '密码' : '密码（至少 8 位）'} type="password"
        required minLength={mode === 'register' ? 8 : undefined} disabled={submitting} value={password} onChange={event => setPassword(event.target.value)}
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'} aria-describedby={error ? 'login-error' : undefined} /></label>
      {error && <div className="status-panel error" id="login-error" role="alert"><h2>身份核验未完成</h2><p>{error}</p></div>}
      <button type="submit" className="button" disabled={submitting}>{submitting ? '正在核验身份' : mode === 'login' ? '登录账号' : '注册并登录'}</button>
      <button type="button" className="button secondary" disabled={submitting} onClick={() => {
        setMode(mode === 'login' ? 'register' : 'login'); setPassword(''); setError(null);
      }}>{mode === 'login' ? '没有账号，去注册' : '已有账号，去登录'}</button>
    </form>}
    <div className="note blue"><ShieldCheck size={17} /><p>知道患儿标识不代表已取得资料访问权。</p></div>
    <button className="text-action center" onClick={() => navigate('/account/data-use')}>了解资料如何使用<ChevronRight size={15} /></button>
  </UI2FamilyFrame>;
}
