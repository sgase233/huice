import { useLayoutEffect, useRef } from 'react';

export default function WorkflowIndicator({ page }: { page: string }) {
  const indicator = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const element = indicator.current;
    const nav = element?.parentElement;
    const selected = nav?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!element || !nav || !selected) return;
    const position = () => {
      element.style.width = `${selected.offsetWidth}px`;
      element.style.height = `${selected.offsetHeight}px`;
      element.style.transform = `translate(${selected.offsetLeft}px, ${selected.offsetTop}px)`;
      element.style.opacity = '1';
    };
    position();
    const frame = requestAnimationFrame(() => element.classList.add('is-ready'));
    const observer = new ResizeObserver(position);
    observer.observe(nav);
    observer.observe(selected);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [page]);
  return <span ref={indicator} className="workflow-indicator" aria-hidden="true" />;
}
