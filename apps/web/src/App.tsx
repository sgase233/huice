import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import AuthApp from './AuthApp';
import HomePage from './pages/HomePage';
import './styles/home.css';

const isWorkbench = (url: URL) => ['#enterprise', '#opportunities', '#policy', '#report', '#trace', '#admin', '#reviewer', '#real-main'].includes(url.hash);

export default function App() {
  const [hash, setHash] = useState(location.hash);
  const previousSurface = useRef(isWorkbench(new URL(location.href)));
  const leaving = useRef<Animation | null>(null);
  useLayoutEffect(() => {
    const surface = isWorkbench(new URL(location.href));
    let pending = false;
    try { pending = sessionStorage.getItem('huice-surface-fade') === '1'; sessionStorage.removeItem('huice-surface-fade'); } catch { /* Navigation still works without session storage. */ }
    if (pending || surface !== previousSurface.current) {
      leaving.current?.cancel();
      leaving.current = null;
      if (!matchMedia('(prefers-reduced-motion: reduce)').matches) document.getElementById('root')?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 400, easing: 'ease-in-out' });
    }
    previousSurface.current = surface;
  }, [hash]);
  useEffect(() => {
    const fadeNavigation = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || !(event.target instanceof Element)) return;
      const link = event.target.closest<HTMLAnchorElement>('a[href]');
      if (!link || link.target || link.hasAttribute('download')) return;
      const destination = new URL(link.href, location.href);
      if (destination.origin !== location.origin || destination.pathname !== '/' || isWorkbench(destination) === isWorkbench(new URL(location.href))) return;
      const sameDocument = destination.pathname === location.pathname && destination.search === location.search;
      const navigate = () => {
        if (!sameDocument) { location.assign(destination.href); return; }
        history.pushState(history.state, '', destination.href);
        window.scrollTo({ top: 0, behavior: 'instant' });
        window.dispatchEvent(new HashChangeEvent('hashchange'));
      };
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
        if (sameDocument) { event.preventDefault(); navigate(); }
        return;
      }
      event.preventDefault();
      if (leaving.current) return;
      const root = document.getElementById('root');
      if (!root) { navigate(); return; }
      const animation = root.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 400, easing: 'ease-in-out', fill: 'forwards' });
      leaving.current = animation;
      void animation.finished.then(() => {
        try { sessionStorage.setItem('huice-surface-fade', '1'); } catch { /* The destination remains usable without an entrance animation. */ }
        navigate();
      }, () => {});
    };
    document.addEventListener('click', fadeNavigation);
    return () => { document.removeEventListener('click', fadeNavigation); leaving.current?.cancel(); };
  }, []);
  useEffect(() => {
    const ripple = (event: PointerEvent) => {
      if (event.button !== 0 || !(event.target instanceof Element) || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const button = event.target.closest<HTMLElement>('.app-shell .main-content button');
      if (!button || button.matches(':disabled')) return;
      const rect = button.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const size = 2 * Math.hypot(Math.max(x, rect.width - x), Math.max(y, rect.height - y));
      button.style.setProperty('--ripple-x', `${x}px`);
      button.style.setProperty('--ripple-y', `${y}px`);
      button.style.setProperty('--ripple-size', `${size}px`);
      button.classList.remove('is-rippling');
      void button.offsetWidth;
      button.classList.add('is-rippling');
    };
    document.addEventListener('pointerdown', ripple);
    return () => document.removeEventListener('pointerdown', ripple);
  }, []);
  useEffect(() => {
    // The workbench may restore its hash after the user cancels leaving an unsaved review.
    const update = () => setTimeout(() => setHash(location.hash), 0);
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  return ['#enterprise', '#opportunities', '#policy', '#report', '#trace', '#admin', '#reviewer', '#real-main'].includes(hash) ? <AuthApp /> : <HomePage />;
}
