import { useId, useRef, useState } from 'react';
import { flushSync } from 'react-dom';

export default function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark');
  const button = useRef<HTMLButtonElement>(null);
  const maskId = useId();
  const switching = useRef(false);

  const toggle = () => {
    if (switching.current) return;
    const next = !dark;
    const apply = () => {
      document.documentElement.dataset.theme = next ? 'dark' : 'light';
      try { localStorage.setItem('huice-theme', next ? 'dark' : 'light'); } catch { /* Theme remains active for this page. */ }
      flushSync(() => setDark(next));
    };
    if (!document.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) { apply(); return; }
    const rect = button.current!.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    switching.current = true;
    const transition = document.startViewTransition(apply);
    void transition.ready.then(() => document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 600, easing: 'ease-in-out', pseudoElement: '::view-transition-new(root)' },
    )).catch(() => {});
    void transition.finished.then(() => { switching.current = false; }, () => { switching.current = false; });
  };

  return <button ref={button} className="theme-toggle" type="button" aria-label={dark ? '切换浅色模式' : '切换深色模式'} aria-pressed={dark} title={dark ? '切换浅色模式' : '切换深色模式'} onClick={toggle}>
    <svg viewBox="0 0 32 32" aria-hidden="true" className="theme-toggle-icon">
      <defs><mask id={maskId}><rect width="32" height="32" fill="white" /><circle className="theme-moon-cutout" cx="25" cy="7" r="8" fill="black" /></mask></defs>
      <g className="theme-sun-rays" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M16 2v3M16 27v3M2 16h3M27 16h3M6.1 6.1l2.1 2.1m15.6 15.6 2.1 2.1M25.9 6.1l-2.1 2.1M8.2 23.8l-2.1 2.1" /></g>
      <circle className="theme-sun-disc" cx="16" cy="16" r="8" fill="currentColor" mask={`url(#${maskId})`} />
    </svg>
  </button>;
}
