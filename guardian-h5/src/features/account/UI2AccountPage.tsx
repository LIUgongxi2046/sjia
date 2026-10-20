import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight, LogOut } from '../../components/ThreeDIcon';
import { Bell, ShieldCheck, UserRound, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { RichThemePreference } from '../../components/RichThemePreference';
import { useAccess } from '../../state/access';
import { useRichTheme } from '../../state/richTheme';
import logo from '../../../../ui-delivery/identity/2x/logo-embrace@2x.png';

export function UI2AccountPage() {
  const access = useAccess();
  const { preference, changePreference } = useRichTheme();
  const { principalId, accountStatus, logout } = access;
  const currentRelation = access.relations.find(relation => relation.patient_id === access.patientId);
  const canSelectPreset = !access.loading && !access.error && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion)
    && !!currentRelation?.actions.includes('select_alert_preset');
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loggedIn = accountStatus === 'active' && !!principalId;
  async function signOut() {
    if (submitting) return;
    setSubmitting(true); setError(null);
    try { await logout(); navigate('/login', { replace: true }); }
    catch (error) { if (!(error instanceof Error)) throw error; setError(error.message); }
    finally { setSubmitting(false); }
  }
  return <UI2FamilyFrame title="我的" screenId="UI2-15" note="账号、患儿与提醒管理。" backTo="/home" primary="account">
    <button className="rich-profile" onClick={() => navigate('/login')}><span className="account-avatar"><UserRound size={49} /></span>
      <span className="rich-profile-copy"><strong className="rich-profile-name">{loggedIn ? '本人账号' : '尚未登录'}</strong><small className="rich-profile-description">{loggedIn ? '已登录 · 查看本人关联与访问范围' : '登录后查看本人关联与访问范围'}</small></span><span className="badge">{loggedIn ? '已登录' : '登录账号'}</span>
    </button>
    <section className="card">
      <button className="list-row" onClick={() => navigate('/associations')}><span className="row-icon"><UsersRound size={30} /></span><span className="row-text"><strong>患儿管理</strong><small>申请、权限与关联状态</small></span><ChevronRight size={17} /></button>
      <button className="list-row" onClick={() => navigate('/account/data-use')}><span className="row-icon"><ShieldCheck size={30} /></span><span className="row-text"><strong>资料使用说明</strong><small>用途、保存与删除</small></span><ChevronRight size={17} /></button>
    </section>
    <section className="card">
      <button className="list-row" onClick={() => navigate('/account/reminders')}><span className="row-icon"><Bell size={30} /></span><span className="row-text"><strong>资料补充提醒</strong><small>按自己的安排继续记录 · {loggedIn ? '设置状态尚未读取' : '默认关闭'}</small></span><ChevronRight size={17} /></button>
      <button className="list-row" onClick={() => navigate('/login')}><span className="row-icon"><UserRound size={30} /></span><span className="row-text"><strong>账号与登录</strong><small>查看当前登录身份</small></span><ChevronRight size={17} /></button>
    </section>
    {canSelectPreset && <section className="card"><button className="list-row" onClick={() => navigate('/alert-presets')}><span className="row-icon"><Bell size={30} /></span><span className="row-text"><strong>提醒方案</strong><small>选择当前获准的提醒方案</small></span><ChevronRight size={17} /></button></section>}
    <section className="card" aria-label="外观设置"><RichThemePreference value={preference} onChange={changePreference} /></section>
    <div className="brand-footer rich-brand-footer"><img src={logo} alt="sJIA 守护者联盟标志" /><span>sJIA 守护者联盟<small>儿童健康记录</small></span></div>
    {error && <section className="status-panel error" role="alert"><h2>退出登录未完成</h2><p>{error}</p></section>}
    {loggedIn && <button className="button secondary" disabled={submitting} onClick={() => void signOut()}><LogOut size={24} />{submitting ? '正在退出登录' : '退出登录'}</button>}
  </UI2FamilyFrame>;
}
