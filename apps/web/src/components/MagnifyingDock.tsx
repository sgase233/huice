import { useRef, useState, type ReactNode } from 'react';
import WorkflowIndicator from './WorkflowIndicator';

export default function MagnifyingDock({ page, real = false, children }: { page: string; real?: boolean; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [tip, setTip] = useState<{ label: string; top: number } | null>(null);
  const [tipVisible, setTipVisible] = useState(false);
  const nav = useRef<HTMLElement>(null);

  // Adapted from Bencho Magnifying Dock (MIT): measure centres on the vertical axis.
  // The raised cosine lets neighbouring glyphs ease together.
  const highlight = (item: HTMLElement | null) => {
    const items = Array.from(nav.current?.querySelectorAll<HTMLElement>('.dock-item') ?? []);
    const active = item ? items.indexOf(item) : -1;
    items.forEach((element, index) => {
      const distance = Math.abs(index - active);
      const factor = active < 0 || distance > 2 ? 0 : (1 + Math.cos(Math.PI * distance / 3)) / 2;
      element.style.setProperty('--dock-factor', String(factor));
    });
    if (item) setTip({ label: item.getAttribute('aria-label') ?? '', top: item.offsetTop + item.offsetHeight / 2 });
    setTipVisible(!!item);
  };

  return <nav ref={nav} className={`side-nav${real ? ' real-nav' : ''}${collapsed ? ' dock-collapsed' : ''}`} aria-label="主导航"
    onPointerMove={event => {
      if (!collapsed || event.pointerType !== 'mouse') return;
      const items = Array.from(nav.current?.querySelectorAll<HTMLElement>('.dock-item') ?? []);
      const item = event.target instanceof Element ? event.target.closest<HTMLElement>('.dock-item') : null;
      if (!item) { highlight(null); return; }
      const nearest = items.reduce((best, current) => {
        const distance = (element: HTMLElement) => { const rect = element.getBoundingClientRect(); return Math.abs(event.clientY - (rect.top + rect.height / 2)); };
        return distance(current) < distance(best) ? current : best;
      }, items[0]);
      highlight(nearest);
    }}
    onPointerLeave={() => highlight(null)}
    onFocusCapture={event => { if (collapsed && event.target instanceof HTMLElement && event.target.classList.contains('dock-item')) highlight(event.target); }}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) highlight(null); }}>
    <div className="dock-heading"><button type="button" className="dock-toggle" aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'} aria-expanded={!collapsed} onClick={() => { highlight(null); setCollapsed(value => !value); }}>
      <span className="dock-menu" aria-hidden="true"><span /><span /><span /></span>
    </button><div className="nav-caption">工作流程</div></div>
    <WorkflowIndicator page={page} />
    {children}
    <span className="dock-tip" aria-hidden="true" data-visible={collapsed && tipVisible} style={tip ? { top: tip.top } : undefined}>{tip?.label}</span>
  </nav>;
}
