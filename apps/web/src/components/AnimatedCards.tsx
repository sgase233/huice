import { useLayoutEffect, useState } from 'react';
import type { ReactElement } from 'react';

type Entry = { key: string; element: ReactElement; entering: boolean; leaving: boolean };

export default function AnimatedCards({ items, hideLeavingContent = false }: { items: ReactElement[]; hideLeavingContent?: boolean }) {
  const [entries, setEntries] = useState<Entry[]>(() => items.map(element => ({ key: String(element.key), element, entering: false, leaving: false })));
  useLayoutEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setEntries(previous => {
      const next = new Map(items.map(element => [String(element.key), element]));
      const current = previous.flatMap(entry => {
        const element = next.get(entry.key);
        if (element) { next.delete(entry.key); return [{ ...entry, element, leaving: false }]; }
        return reduced ? [] : [{ ...entry, entering: false, leaving: true }];
      });
      for (const [key, element] of next) current.push({ key, element, entering: !reduced, leaving: false });
      return current;
    });
    if (reduced) return;
    const timeout = window.setTimeout(() => setEntries(current => current.filter(entry => !entry.leaving).map(entry => entry.entering ? { ...entry, entering: false } : entry)), 400);
    return () => window.clearTimeout(timeout);
  }, [items]);
  return <>{entries.map(entry => <div key={entry.key} className={`motion-card-slot${entry.entering ? ' is-entering' : ''}${entry.leaving ? ' is-removing' : ''}${entry.leaving && hideLeavingContent ? ' is-redacted' : ''}`} aria-hidden={entry.leaving || undefined} inert={entry.leaving}><div>{entry.element}</div></div>)}</>;
}
