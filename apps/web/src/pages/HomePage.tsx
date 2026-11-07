import ThemeToggle from '../components/ThemeToggle';

const steps = [
  { title: '填写企业画像', text: '记录企业基本情况与相关事实，区分已填写与尚不清楚的信息。', icon: 'profile' },
  { title: '查看政策机会', text: '浏览政策候选，按类型和窗口状态筛选需要关注的事项。', icon: 'search' },
  { title: '逐条核对依据', text: '查看条件、结果和来源；资料不足或存在冲突时，继续人工核对。', icon: 'document' },
  { title: '整理准备清单', text: '根据政策类型查看材料与报告，为后续复核做好准备。', icon: 'list' },
] as const;

function StepIcon({ name }: { name: typeof steps[number]['icon'] }) {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'profile' && <><circle cx="16" cy="10" r="4" /><path d="M8 25v-2a8 8 0 0 1 16 0v2H8Z" /></>}
    {name === 'search' && <><circle cx="14" cy="14" r="7" /><path d="m19 19 6 6M11 14h6M14 11v6" /></>}
    {name === 'document' && <><path d="M9 4h10l4 4v20H9zM19 4v5h4M12 14h8M12 18h8M12 22h5" /></>}
    {name === 'list' && <><path d="M10 6h15M10 13h15M10 20h15M10 27h15M4 6l1 1 2-2M4 13l1 1 2-2M4 20l1 1 2-2M4 27l1 1 2-2" /></>}
  </svg>;
}

export default function HomePage() {
  const [slide, setSlide] = useState(0);
  const [interacting, setInteracting] = useState(false);
  const [inView, setInView] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const carousel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting));
    if (carousel.current) observer.observe(carousel.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (interacting || reducedMotion || !inView) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) setSlide(current => current + 1);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [interacting, reducedMotion, inView]);
  return <div className="home">
    <a className="skip-link" href="#home-main">跳到主要内容</a>
    <header className="home-header"><div className="home-wrap home-header-inner">
      <a className="home-brand" href="/" aria-label="惠策协同首页"><span className="home-brand-mark">策</span><span><strong>惠策协同</strong><small>企业政策核对工作台</small></span></a>
      <div className="header-actions"><nav aria-label="首页导航"><a href="#how-it-works">使用流程</a><a href="#about">使用说明</a><a className="home-nav-action" href="#enterprise">进入工作台 <span aria-hidden="true">→</span></a></nav><ThemeToggle /></div>
    </div></header>
    <main id="home-main">
      <section className="home-hero"><div className="home-wrap home-hero-grid">
        <div className="home-hero-copy"><p className="home-intro">为企业梳理政策信息</p><h1><span className="home-reveal-line"><span>看懂政策，</span></span><span className="home-reveal-line home-reveal-accent"><span>从核对依据开始。</span></span></h1><p className="home-lead">从企业情况出发，查看政策机会、核对适用条件，整理下一步需要准备的资料。</p><div className="home-actions"><a className="home-primary" href="#enterprise">开始使用工作台 <span aria-hidden="true">→</span></a><a className="home-text-link" href="?mode=demo#enterprise">先体验固定样例 <span aria-hidden="true">↗</span></a></div><p className="home-note">当前工作台使用合成企业数据；固定样例不代表真实资格判断。</p></div>
        <div className="home-carousel" ref={carousel} role="region" aria-roledescription="轮播图" aria-label="首页图片预留：企业办公协作、企业资料整理和团队交流讨论" onMouseEnter={() => setInteracting(true)} onMouseLeave={() => setInteracting(false)}>
          <div className="home-carousel-ring" data-slide={slide % slides.length} aria-hidden="true">
            {slides.map((item, index) => <div className="home-image-placeholder" key={item.title} data-position={(index - slide % slides.length + slides.length) % slides.length}><div className="home-image-label"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="4" y="6" width="24" height="20" rx="2"/><circle cx="12" cy="12" r="2"/><path d="m6 23 6-6 4 4 4-5 6 7"/></svg><span>图片预留：{item.title}</span><small>{item.detail}</small></div></div>)}
          </div>
        </div>
      </div></section>
      <section className="home-process home-wrap" id="how-it-works"><div className="home-section-heading"><div><h2>把复杂政策，拆成清晰的步骤</h2><p>从了解企业情况到整理材料，每一步都可以回到依据继续核对。</p></div><span className="home-section-aside">企业政策核对流程</span></div><ol className="home-steps">{steps.map((step, index) => <li key={step.title}><div className="home-step-top"><span className="home-step-icon"><StepIcon name={step.icon} /></span><span className="home-step-number">0{index + 1}</span></div><h3>{step.title}</h3><p>{step.text}</p></li>)}</ol></section>
      <section className="home-about" id="about"><div className="home-wrap home-about-grid"><div><h2>结论之外，<br />更要看见依据。</h2><p>政策核对需要结合企业事实、政策原文与人工复核。惠策协同帮助整理信息，让需要补充和核实的地方更清楚。</p></div><div className="home-about-detail"><h3>使用前请了解</h3><p>政策信息用于辅助整理，不构成税务专业意见；实际办理前，请企业经办人员核对官方通知并进行专业复核。</p><a href="#enterprise">进入工作台 <span aria-hidden="true">→</span></a></div></div></section>
    </main>
    <footer className="home-footer"><div className="home-wrap"><strong>惠策协同</strong><span>让政策信息更清晰，让每一步核对有迹可循。</span></div></footer>
  </div>;
}
import { useEffect, useRef, useState } from 'react';

const slides = [
  { title: '企业办公协作', detail: '白灰底色与暖橙点缀 · 横向场景' },
  { title: '企业资料整理', detail: '暖白浅灰与少量暖橙 · 桌面细节' },
  { title: '团队交流讨论', detail: '自然光与暖橙点缀 · 人物场景' },
] as const;
