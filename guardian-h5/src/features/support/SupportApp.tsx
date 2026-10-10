import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Route, Routes, useNavigate, useSearchParams } from 'react-router';
import { CircleAlert, LoaderCircle, LockKeyhole, WifiOff } from 'lucide-react';
import { UI2Frame } from '../../components/UI2Frame';
import { ApiError, setSessionCsrfToken } from '../../api/client';
import { bootstrap, logout } from '../../api/endpoints';
import { loginSupport, readSupportSession } from '../../api/purpose';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import { PurposeCopyPage } from './PurposeCopyPage';
import { DatasetPage } from './DatasetPage';
import { AssessmentPage } from './AssessmentPage';
import { VerificationPage } from './VerificationPage';
import { PublicationPage } from './PublicationPage';
import { SourcesPage } from './SourcesPage';
import { NotificationPage } from './NotificationPage';
import logo from '../../../../ui-delivery/identity/2x/logo-embrace@2x.png';

export type SupportReadState = 'default' | 'loading' | 'permission' | 'error' | 'offline';

export function SupportReadStatus({ state, error, onRefresh }: {
  state: SupportReadState; error: string | null; onRefresh: () => void;
}) {
  if (state === 'default') return null;
  const title = { loading: '正在读取当前资料', permission: '当前访问关系需要核验',
    error: '暂时无法取得资料', offline: '网络连接已中断' }[state];
  const description = { loading: '读取完成后显示当前获准内容。', permission: '重新取得有效权限后才能查看资料。',
    error: '重新读取前，现有资料状态保持未知。', offline: '尚未保存的输入保留在当前页面。' }[state];
  const Icon = state === 'loading' ? LoaderCircle : state === 'permission' ? LockKeyhole : state === 'offline' ? WifiOff : CircleAlert;
  return <section className={`status-panel ${state}`} role="status"><div className="status-top"><Icon size={22} /><span>页面状态</span></div>
    <h2>{title}</h2><p>{error ?? description}</p>
    {state !== 'loading' && <button className="text-action" onClick={onRefresh}>{state === 'permission' ? '核验访问权限' : '重新读取'}</button>}
  </section>;
}

function SupportLogin({ onAuthenticated }: { onAuthenticated: (session: SupportSessionView) => void }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const composing = useRef(false);
  const controller = useRef<AbortController>();
  useEffect(() => () => controller.current?.abort(), []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || composing.current) return;
    setPending(true); setError(null);
    controller.current = new AbortController();
    try {
      const token = await bootstrap();
      setSessionCsrfToken(token.csrf_token);
      const session = await loginSupport({ username, password }, controller.current.signal);
      setPassword(''); onAuthenticated(session); navigate('/support/data-use', { replace: true });
    } catch (failure) {
      if (!controller.current.signal.aborted) setError(failure instanceof Error ? failure.message : '登录服务读取失败。');
    } finally { setPending(false); }
  }
  return <UI2Frame title="登录" screenId="UI2-17" note="登录后仅访问当前岗位与病例范围内的资料。" onBack={() => navigate('/account')}>
    <div className="login-brand"><img src={logo} alt="sJIA 守护者联盟标志" />
      <h2>sJIA 守护者联盟</h2><p>为孩子，保存每次重要的记录。</p></div>
    <form className="card form-card" onSubmit={event => void submit(event)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <h3>登录后访问本人获准资料</h3>
      <label className="field"><span>账号</span><input required autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label className="field"><span>密码</span><input required type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /></label>
      {error && <p role="alert">{error}</p>}
      <button className="button" disabled={pending} type="submit">{pending ? '正在核验身份' : '登录账号'}</button>
    </form>
    <div className="note"><LockKeyhole size={17} /><p>知道患儿标识不代表已取得资料访问权。</p></div>
  </UI2Frame>;
}

export default function SupportApp() {
  const navigate = useNavigate();
  const [parameters] = useSearchParams();
  const [session, setSession] = useState<SupportSessionView | null>(null);
  const [state, setState] = useState<SupportReadState>('loading');
  const [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  const controller = useRef<AbortController>();
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    controller.current?.abort(); controller.current = new AbortController();
    setState('loading'); setError(null);
    try {
      const result = await readSupportSession(controller.current.signal);
      if (current !== sequence.current || document.visibilityState !== 'visible') return;
      setSession(result); setSessionCsrfToken(result.csrf_token); setState('default');
    } catch (failure) {
      if (current !== sequence.current || controller.current.signal.aborted) return;
      setSession(null); setSessionCsrfToken(null);
      setState(failure instanceof ApiError && [401, 403].includes(failure.status) ? 'permission' : navigator.onLine ? 'error' : 'offline');
      setError(failure instanceof Error ? failure.message : '访问权限读取失败。');
    }
  }, []);
  useEffect(() => {
    void refresh();
    const restore = () => {
      if (document.visibilityState === 'visible') void refresh();
      else { sequence.current += 1; controller.current?.abort(); setState('loading'); }
    };
    window.addEventListener('pageshow', restore); document.addEventListener('visibilitychange', restore);
    return () => { sequence.current += 1; controller.current?.abort(); window.removeEventListener('pageshow', restore); document.removeEventListener('visibilitychange', restore); };
  }, [refresh]);
  function authenticated(result: SupportSessionView) { setSession(result); setState('default'); setError(null); }
  async function signOut() {
    await logout(); sequence.current += 1; controller.current?.abort();
    setSession(null); setSessionCsrfToken(null); setState('permission'); navigate('/support/login', { replace: true });
  }
  return <Routes>
    <Route path="login" element={<SupportLogin onAuthenticated={authenticated} />} />
    <Route path="data-use" element={<PurposeCopyPage key={session ? `${session.principal_id}:${session.capabilities.map(item => item.capability_id).sort().join(',')}:${parameters.get('copy_id') ?? 'none'}:${parameters.get('file_id') ?? 'none'}` : 'anonymous'}
      session={session} accessState={state} accessError={error} onRefresh={refresh} onLogout={() => void signOut()} />} />
    <Route path="datasets" element={<DatasetPage session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="assessment" element={<AssessmentPage key={session?.principal_id ?? 'anonymous'}
      session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="verification" element={<VerificationPage key={session?.principal_id ?? 'anonymous'}
      session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="publication" element={<PublicationPage key={session?.principal_id ?? 'anonymous'}
      session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="sources" element={<SourcesPage key={session?.principal_id ?? 'anonymous'} session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="deliveries" element={<NotificationPage key={session?.principal_id ?? 'anonymous'}
      session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="delivery" element={<NotificationPage key={session?.principal_id ?? 'anonymous'}
      session={session} accessState={state} accessError={error} onRefresh={refresh} />} />
    <Route path="*" element={<UI2Frame title="页面不存在" screenId="UI2-24" note="提供准确的返回入口。" onBack={() => navigate('/account')}>
      <section className="card"><div className="empty"><h3>这个页面暂不可用</h3><p>链接可能不完整，可以返回当前可用入口。</p>
        <button className="text-action" onClick={() => navigate('/support/data-use')}>查看用途与副本</button></div></section>
    </UI2Frame>} />
  </Routes>;
}
