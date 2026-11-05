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
      { duration: 240, easing: 'cubic-bezier(.16, 1, .3, 1)' },
    );
    return () => animation?.cancel();
  }, [page]);
  return content;
}
