import { useEffect, useRef, useState } from 'react';
import { ApiRequestError } from '../api/client';

function Avatar({ large = false }: { large?: boolean }) {
  return <span className={`account-avatar${large ? ' account-avatar-large' : ''}`} aria-hidden="true"><svg viewBox="0 0 48 48" fill="none"><circle cx="24" cy="17" r="7" fill="currentColor" /><path d="M10 40c.8-8.2 6.2-12.5 14-12.5S37.2 31.8 38 40" fill="currentColor" /></svg></span>;
}

type Props = { name: string; canChangePassword: boolean; onLogout: () => Promise<void>; onChangePassword: (current: string, next: string) => Promise<void>; confirmLeave: () => boolean };
export default function AccountDrawer({ name, canChangePassword, onLogout, onChangePassword, confirmLeave }: Props) {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const trigger = useRef<HTMLButtonElement>(null);
  const drawer = useRef<HTMLElement>(null);
  function close() { if (busy) return; setOpen(false); setCurrent(''); setNext(''); setConfirm(''); setError(''); trigger.current?.focus(); }
  useEffect(() => {
    if (!open) return;
    drawer.current?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key !== 'Tab' || !drawer.current) return;
      const focusable = Array.from(drawer.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href]'));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener('keydown', keydown);
    return () => document.removeEventListener('keydown', keydown);
  }, [open, busy]);
  async function change(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (next !== confirm) { setError('两次输入的新密码不一致，请重新确认。'); return; }
    if (!confirmLeave()) return;
    setBusy(true); setError('');
    try { await onChangePassword(current, next); }
    catch (e) {
      const fields = e instanceof ApiRequestError && Array.isArray(e.details.fieldErrors) ? e.details.fieldErrors : [];
      const text = fields.map(item => item && typeof item === 'object' && 'message' in item && typeof item.message === 'string' ? item.message : '').filter(Boolean).join('；');
      setError(text || (e instanceof ApiRequestError && e.status === 401 ? '当前密码不正确或会话已失效，请核对后重试。' : '密码修改未完成，请检查输入后重试。'));
    } finally { setBusy(false); }
  }
  async function logout() {
    if (busy || !confirmLeave()) return;
    setBusy(true); setError('');
    try { await onLogout(); } catch { setError('退出未完成，请稍后重试。'); } finally { setBusy(false); }
  }
  return <>
    <div className="account-trigger-wrap"><button ref={trigger} type="button" className="account-trigger" aria-label="账号与退出" aria-haspopup="dialog" aria-expanded={open} aria-describedby="account-tooltip" onClick={() => setOpen(true)}><Avatar /></button><span className="account-tooltip" id="account-tooltip" role="tooltip">{name}</span></div>
    <div className={`account-overlay${open ? ' is-open' : ''}`} aria-hidden={!open} onClick={close} />
    <aside ref={drawer} className={`account-drawer${open ? ' is-open' : ''}`} role="dialog" aria-modal="true" aria-labelledby="account-drawer-title" aria-hidden={!open} inert={!open} tabIndex={-1}>
      <button className="account-close" type="button" aria-label="关闭账号面板" disabled={busy} onClick={close}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg></button>
      <div className="account-identity"><Avatar large /><h2 id="account-drawer-title">{name}</h2></div>
      {canChangePassword ? <form className="account-form" onSubmit={event => void change(event)}><label>当前密码<input type="password" autoComplete="current-password" required value={current} onChange={e => setCurrent(e.target.value)} /></label><label>新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={next} onChange={e => setNext(e.target.value)} /></label><label>确认新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={confirm} onChange={e => setConfirm(e.target.value)} /></label>{error && <p className="account-error" role="alert">{error}</p>}<div className="account-actions"><button type="submit" disabled={busy}>修改密码</button><button type="button" disabled={busy} onClick={() => void logout()}>退出登录</button></div></form> : <div className="account-form"><p>当前使用服务端演示。</p>{error && <p className="account-error" role="alert">{error}</p>}<div className="account-actions"><button type="button" disabled={busy} onClick={() => void logout()}>退出演示</button></div></div>}
    </aside>
  </>;
}
