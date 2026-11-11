import { useEffect, useRef } from 'react';

export default function useWorkflowTransition(page: string) {
  const content = useRef<HTMLElement>(null);
  const previous = useRef(page);
  useEffect(() => {
    if (previous.current === page) return;
    previous.current = page;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const animation = content.current?.animate(
      [{ opacity: .7, transform: 'translateY(6px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: 960, easing: 'cubic-bezier(.16, 1, .3, 1)' },
    );
    return () => animation?.cancel();
  }, [page]);
  useEffect(() => {
    const root = content.current;
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || window.matchMedia('(hover: none)').matches || !('IntersectionObserver' in window)) return;
    const seen = new WeakSet<Element>();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) continue;
        entry.target.animate(
          [{ opacity: .2, transform: 'translateY(36px)' }, { opacity: 1, transform: 'translateY(0)' }],
          { duration: 1200, easing: 'cubic-bezier(.22, .7, .32, 1)' },
        );
      }
    });
    const observeCards = () => {
      root.querySelectorAll('.panel, .fact-card').forEach(card => {
        if (seen.has(card)) return;
        seen.add(card);
        const rect = card.getBoundingClientRect();
        if (rect.width && rect.height && (rect.top >= window.innerHeight || rect.bottom <= 0)) observer.observe(card);
      });
    };
    observeCards();
    const changes = new MutationObserver(observeCards);
    changes.observe(root, { childList: true, subtree: true });
    return () => { changes.disconnect(); observer.disconnect(); };
  }, [page]);
  return content;
}
