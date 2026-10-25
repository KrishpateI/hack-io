import { useSyncExternalStore } from 'react';
import { COMPACT_MAX, DESKTOP_MIN, viewportTier, type ViewportTier } from '../layout';

const QUERIES = [`(max-width: ${COMPACT_MAX - 1}px)`, `(max-width: ${DESKTOP_MIN - 1}px)`];

function hasMatchMedia(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function';
}

function tierFromMatchMedia(): ViewportTier {
  const [compact, wide] = QUERIES.map((q) => window.matchMedia(q).matches);
  if (compact) return 'compact';
  if (wide) return 'wide';
  return 'desktop';
}

function getSnapshot(): ViewportTier {
  if (typeof window === 'undefined') return 'desktop';
  // jsdom has no matchMedia; fall back to innerWidth.
  if (hasMatchMedia()) {
    try {
      return tierFromMatchMedia();
    } catch {
      /* fall through */
    }
  }
  return viewportTier(window.innerWidth);
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  // `resize` always listens so tests that change innerWidth and dispatch resize work too.
  window.addEventListener('resize', onChange);
  const lists: MediaQueryList[] = [];
  if (hasMatchMedia()) {
    for (const q of QUERIES) {
      const mql = window.matchMedia(q);
      if (typeof mql.addEventListener === 'function') mql.addEventListener('change', onChange);
      else if (typeof mql.addListener === 'function') mql.addListener(onChange);
      lists.push(mql);
    }
  }
  return () => {
    window.removeEventListener('resize', onChange);
    for (const mql of lists) {
      if (typeof mql.removeEventListener === 'function') mql.removeEventListener('change', onChange);
      else if (typeof mql.removeListener === 'function') mql.removeListener(onChange);
    }
  };
}

/** Current viewport tier: compact < 768, wide < 1280, desktop otherwise. */
export function useViewportTier(): ViewportTier {
  return useSyncExternalStore(subscribe, getSnapshot, () => 'desktop');
}
