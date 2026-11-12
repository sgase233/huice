import { useEffect, useState } from 'react';
import { api, ApiRequestError, invalidateRequests, setCsrfToken } from './api/client';
import type { SessionResponse } from './api/types';
import RealApp from './RealApp';
import ThemeToggle from './components/ThemeToggle';

function AuthLayout({ children }: { children: React.ReactNode }) {
  return <div className="app-shell"><header className="site-header"><div className="header-inner"><a className="brand" href="/">惠策协同</a><ThemeToggle /></div></header><main className="main-content auth-content">{children}</main></div>;
}

export default function AuthApp() {
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState(false);
  const [demo, setDemo] = useState(false);
  const [gate, setGate] = useState(false);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [company, setCompany] = useState('');
  const [region, setRegion] = useState('');
  const [industry, setIndustry] = useState('');
  const [start, setStart] = useState('2026-01-01');
  const [end, setEnd] = useState('2026-12-31');
  const [reviewerKey, setReviewerKey] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  function showError(error: unknown) {
    if (error instanceof ApiRequestError && error.code === 'STALE_REQUEST') return;
    if (error instanceof ApiRequestError && (error.code === 'DEMO_SESSION_REQUIRED' || error.code === 'DEMO_SESSION_INVALID')) setNotice('演示门禁已失效，请从演示入口重新进入后重试。');
    else if (error instanceof ApiRequestError && (error.code === 'SESSION_INVALID' || error.code === 'UNAUTHENTICATED')) setNotice('会话已失效，请重新登录。');
    else if (error instanceof ApiRequestError) {
      const fields = Array.isArray(error.details.fieldErrors) ? error.details.fieldErrors : [];
      const messages = fields.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item)).map(item => [item.field, item.message].filter(value => typeof value === 'string' && value).join('：')).filter(Boolean);
      setNotice([error.message, ...messages, error.traceId ? `追踪 ID：${error.traceId}` : ''].filter(Boolean).join('；'));
    } else setNotice(error instanceof Error ? error.message : '请求失败，请重试。');
  }
  function clearForm() { setUsername(''); setPassword(''); setNewPassword(''); setReviewerKey(''); setCompany(''); setRegion(''); setIndustry(''); }
  function accept(value: SessionResponse) { setCsrfToken(value.csrfToken); setSession(value); setAccount(false); setGate(false); setDemo(false); clearForm(); }
  function restoreError(error: unknown) {
    // A valid demo identity is not a user session; #61 returns ROLE_FORBIDDEN for /auth/me.
    if (error instanceof ApiRequestError && error.code === 'ROLE_FORBIDDEN') { setGate(false); setSession(null); setCsrfToken(null); setNotice('请登录账号，或进入服务端演示。'); return; }
    if (error instanceof ApiRequestError && (error.code === 'DEMO_SESSION_REQUIRED' || error.code === 'DEMO_SESSION_INVALID')) { setGate(true); showError(error); return; }
    if (error instanceof ApiRequestError && error.code === 'SESSION_INVALID') { setSession(null); setCsrfToken(null); setGate(false); setNotice('会话已失效，请重新登录。'); return; }
    if (error instanceof ApiRequestError && error.code === 'UNAUTHENTICATED') { setGate(false); return; }
    if (!(error instanceof ApiRequestError && (error.code === 'SESSION_INVALID' || error.code === 'UNAUTHENTICATED'))) showError(error);
  }
  useEffect(() => {
    const handle = (event: Event) => {
      const code = (event as CustomEvent<string>).detail;
      invalidateRequests();
      setDemo(false);
      if (code === 'DEMO_SESSION_REQUIRED' || code === 'DEMO_SESSION_INVALID') { setGate(true); setNotice('演示门禁已失效，请从演示入口重新进入后重试。'); }
      else { setCsrfToken(null); setSession(null); setAccount(false); setGate(false); clearForm(); setNotice(demo && code === 'UNAUTHENTICATED' ? '该部署未启用服务端演示，请登录账号。' : '会话已失效，请重新登录。'); }
    };
    window.addEventListener('huice-auth-error', handle);
    return () => window.removeEventListener('huice-auth-error', handle);
  }, [demo]);
  useEffect(() => {
    let active = true;
    api.currentSession().then(result => { if (active) accept(result.data); }).catch(error => { if (active) restoreError(error); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setNotice('');
    try {
      if (mode === 'register') {
        if (!/^[a-z0-9_.-]{3,32}$/.test(username.trim().toLowerCase())) { setNotice('用户名须为 3—32 位字母、数字、下划线、点或连字符。'); return; }
        const period = { startDate: start, endDate: end };
        await api.register({ username: username.trim().toLowerCase(), password, ...(company ? { enterprise: { nameSynthetic: company, period, facts: [{ key: 'region', value: region, unit: 'code', period, source: 'user_input' }, { key: 'industry', value: industry, unit: 'code', period, source: 'user_input' }] } } : {}), ...(reviewerKey ? { reviewerSignupKey: reviewerKey } : {}) });
        setMode('login'); setPassword(''); setReviewerKey(''); setNotice('注册成功，请登录。');
      } else accept((await api.login({ username, password })).data);
    } catch (error) { showError(error); } finally { setBusy(false); }
  }
  async function endSession(change: boolean) {
    setBusy(true); setNotice('');
    try {
      if (change) await api.changePassword({ currentPassword: password, newPassword });
      else await api.logout();
      setSession(null); setCsrfToken(null); clearForm(); setAccount(false); setNotice(change ? '密码已更新，请重新登录。' : '已退出。');
    } catch (error) { showError(error); } finally { setBusy(false); }
  }
  async function enterDemo() {
    setBusy(true); setNotice('');
    try {
      try { await api.logout(); }
      catch (error) {
        if (!(error instanceof ApiRequestError && error.code === 'CSRF_REQUIRED')) throw error;
        const current = await api.currentSession();
        setCsrfToken(current.data.csrfToken);
        await api.logout();
      }
      setCsrfToken(null); clearForm(); setDemo(true);
    }
    catch (error) { showError(error); } finally { setBusy(false); }
  }
  if (loading) return <AuthLayout><p>正在恢复会话…</p></AuthLayout>;
  if (gate) return <AuthLayout><section className="panel"><h1>演示入口已失效</h1><p role="alert">{notice}</p><a href="/">返回演示入口</a><button onClick={() => { setLoading(true); void api.currentSession().then(result => accept(result.data)).catch(restoreError).finally(() => setLoading(false)); }}>重新检查会话</button></section></AuthLayout>;
  if (session && !account) return <RealApp key={session.user.id} user={session.user} onExit={() => { invalidateRequests(); setAccount(true); }} onEnterpriseCreated={id => setSession(current => current ? { ...current, user: { ...current.user, enterpriseId: id } } : current)} />;
  if (demo) return <RealApp user={null} onExit={() => { invalidateRequests(); setDemo(false); }} />;
  return <AuthLayout><section className="panel"><h1>{session ? '账号设置' : mode === 'login' ? '登录' : '注册'}</h1>
    {notice && <p role="alert">{notice}</p>}
    {session ? <><p>当前账号：{session.user.username}</p><button disabled={busy} onClick={() => void endSession(false)}>退出并切换账号</button><form onSubmit={event => { event.preventDefault(); void endSession(true); }}><label>当前密码<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label><label>新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label><button disabled={busy}>修改密码</button></form><button disabled={busy} onClick={() => { setPassword(''); setNewPassword(''); setAccount(false); }}>返回工作台</button></> : <><form onSubmit={event => void submit(event)}><label>用户名<input required value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></label><label>密码<input type="password" required minLength={mode === 'register' ? 12 : 1} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} /></label>{mode === 'register' && <><label>企业名称<input required={!reviewerKey} value={company} onChange={e => setCompany(e.target.value)} /></label><label>地区代码<input required={!!company} value={region} onChange={e => setRegion(e.target.value)} /></label><label>行业代码<input required={!!company} value={industry} onChange={e => setIndustry(e.target.value)} /></label><label>期间开始<input type="date" required value={start} onChange={e => setStart(e.target.value)} /></label><label>期间结束<input type="date" required value={end} onChange={e => setEnd(e.target.value)} /></label><label>审核员注册密钥（仅审核员）<input type="password" value={reviewerKey} onChange={e => setReviewerKey(e.target.value)} /></label><p>企业账号须填写企业名称、地区和行业；审核员可不绑定企业，若填写企业资料则会创建并绑定该企业。用户名规范化后须为 3—32 位字母、数字、下划线、点或连字符。</p></>}<button disabled={busy}>{mode === 'login' ? '登录' : '注册'}</button></form><button disabled={busy} onClick={() => { setPassword(''); setReviewerKey(''); setMode(mode === 'login' ? 'register' : 'login'); setNotice(''); }}>{mode === 'login' ? '注册账号' : '返回登录'}</button></>}
    {!session && <p><button disabled={busy} onClick={() => void enterDemo()}>进入服务端演示</button> {!busy && <a href="?mode=demo#enterprise">使用固定前端样例</a>}</p>}
  </section></AuthLayout>;
}
