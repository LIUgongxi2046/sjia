import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight } from 'lucide-react';
import { Bell, ShieldCheck, UserRound, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { useAccess } from '../../state/access';
import logo from '../../../../ui-delivery/identity/2x/logo-embrace@2x.png';

export function UI2AccountPage() {
  const { principalId, accountStatus, logout } = useAccess();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loggedIn = accountStatus === 'active' && !!principalId;
  async function signOut() {
    if (submitting) return;
    setSubmitting(true); setError(null);
    try { await logout(); navigate('/home', { replace: true }); }
    catch (error) { if (!(error instanceof Error)) throw error; setError(error.message); }
    finally { setSubmitting(false); }
  }
  return <UI2FamilyFrame title="我的" screenId="UI2-15" note="账号、患儿与提醒管理。" backTo="/home" primary="account">
    <section className="account-card"><span className="account-avatar"><UserRound size={49} /></span>
      <h2>管理我的资料</h2><p>{loggedIn ? '已登录，可以查看本人关联与访问范围' : '登录后查看本人关联与访问范围'}</p>
      <button className="button" onClick={() => navigate('/login')}>{loggedIn ? '查看登录身份' : '登录账号'}</button>
    </section>
    <section className="card">
      <button className="list-row" onClick={() => navigate('/associations')}><span className="row-icon"><UsersRound size={30} /></span><span className="row-text"><strong>患儿管理</strong><small>申请、权限与关联状态</small></span><ChevronRight size={17} /></button>
      <button className="list-row" onClick={() => navigate('/account/reminders')}><span className="row-icon"><Bell size={30} /></span><span className="row-text"><strong>资料补充提醒</strong><small>按自己的安排继续记录</small></span><span className="badge neutral">{loggedIn ? '设置状态尚未读取' : '默认关闭'}</span><ChevronRight size={17} /></button>
      <button className="list-row" onClick={() => navigate('/account/data-use')}><span className="row-icon"><ShieldCheck size={30} /></span><span className="row-text"><strong>资料使用说明</strong><small>用途、保存与删除</small></span><ChevronRight size={17} /></button>
    </section>
    <div className="brand-footer"><img src={logo} alt="sJIA 守护者联盟标志" /><span>sJIA 守护者联盟<small>陪伴每一次记录</small></span></div>
    {error && <section className="status-panel error" role="alert"><h2>退出登录未完成</h2><p>{error}</p></section>}
    {loggedIn && <button className="button secondary" disabled={submitting} onClick={() => void signOut()}>{submitting ? '正在退出登录' : '退出登录'}</button>}
  </UI2FamilyFrame>;
}
