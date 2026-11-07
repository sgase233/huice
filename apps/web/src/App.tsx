import { useEffect, useState } from 'react';
import type { EnterpriseInput } from './api/types';
import { demoNotice, demoPolicies, initialEnterprise } from './demo';
import EnterprisePage from './pages/EnterprisePage';
import OpportunitiesPage from './pages/OpportunitiesPage';
import PolicyDetailPage from './pages/PolicyDetailPage';
import ReportPage from './pages/ReportPage';
import AdminReviewPage from './pages/AdminReviewPage';
import RealApp from './RealApp';
import HomePage from './pages/HomePage';
import WorkflowIcon from './components/WorkflowIcon';
import MagnifyingDock from './components/MagnifyingDock';
import useWorkflowTransition from './components/useWorkflowTransition';
import './styles/home.css';

const pages = [
  { id: 'enterprise', label: '企业画像' }, { id: 'opportunities', label: '机会列表' },
  { id: 'policy', label: '政策详情' }, { id: 'report', label: '清单与报告' },
  { id: 'admin', label: '审核预览' },
] as const;
type PageId = typeof pages[number]['id'];
const pageFromHash = (): PageId => {
  const name = location.hash.slice(1);
  return pages.some(page => page.id === name) ? name as PageId : 'enterprise';
};

export default function App() {
  const [hash, setHash] = useState(location.hash);
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
  if (new URLSearchParams(location.search).get('mode') === 'demo') return <DemoApp />;
  return ['#enterprise', '#opportunities', '#policy', '#report', '#trace', '#admin', '#real-main'].includes(hash) ? <RealApp /> : <HomePage />;
}

function DemoApp() {
  const [page, setPage] = useState<PageId>(pageFromHash);
  const mainContent = useWorkflowTransition(page);
  const [enterprise, setEnterprise] = useState<EnterpriseInput>(() => structuredClone(initialEnterprise));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    const onHashChange = () => { setPage(pageFromHash()); window.scrollTo({ top: 0, behavior: 'instant' }); };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);
  const selected = demoPolicies.find(policy => policy.id === selectedId) || null;
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <header className="site-header"><div className="header-inner"><a className="brand" href="#enterprise"><span className="brand-mark" aria-hidden="true">策</span><span><strong>惠策协同</strong><small>企业政策核对工作台</small></span></a><div className="header-caption">阶段一 · 界面验证版 <a href="?#enterprise">切换真实接口</a></div></div></header>
    <div className="shell-layout"><MagnifyingDock page={page}>{pages.map(item => <a className="dock-item" aria-label={item.label} key={item.id} href={`#${item.id}`} aria-current={page === item.id ? 'page' : undefined}><WorkflowIcon name={item.id} /><span className="dock-label">{item.label}</span></a>)}<div className="nav-foot">所有政策与结论均为合成演示内容。</div></MagnifyingDock>
      <main id="main-content" ref={mainContent} className="main-content"><div className="demo-banner" role="status"><span className="banner-dot" aria-hidden="true" />{demoNotice}<span className="banner-detail">固定示例不会随画像自动计算</span></div>
        {page === 'enterprise' && <EnterprisePage value={enterprise} onSave={value => { setEnterprise(value); setSelectedId(null); }} />}
        {page === 'opportunities' && <OpportunitiesPage policies={demoPolicies} selectedId={selectedId} onSelect={id => { setSelectedId(id); location.hash = 'policy'; }} />}
        {page === 'policy' && <PolicyDetailPage policy={selected} />}
        {page === 'report' && <ReportPage policy={selected} />}
        {page === 'admin' && <AdminReviewPage />}
      </main></div>
    <footer className="site-footer">惠策协同 · 合成数据界面验证 · 无真实资格判断</footer>
  </div>;
}
