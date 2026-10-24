/**
 * Request routing rules shared by the service worker (Req 41.1, design D8).
 *
 * `public/sw.js` is plain JavaScript and cannot import this module, so the same logic lives in
 * its `// <routing>` ... `// </routing>` block. `routing.test.ts` evaluates that block and checks
 * it agrees with this file, so the two cannot drift apart.
 *
 * Safety rule: dynamic safety data (routes, reports, messages, check-ins, events, incidents,
 * notices, demo, auth) and realtime channels are NETWORK-ONLY. They are never cached or served
 * stale as if current. Only an explicit allowlist of static API data is cached.
 */
export type RouteClass = 'network-only' | 'swr-api' | 'pmtiles' | 'asset' | 'shell-nav' | 'pass';

/** API prefixes that must never be cached or answered from a cache. Documented, not exhaustive. */
export const NETWORK_ONLY_API: readonly RegExp[] = [
  /^\/api\/routes(\/|$)/,
  /^\/api\/reports(\/|$)/,
  /^\/api\/messages(\/|$)/,
  /^\/api\/checkins(\/|$)/,
  /^\/api\/events(\/|$)/,
  /^\/api\/incidents(\/|$)/,
  /^\/api\/notices(\/|$)/,
  /^\/api\/demo(\/|$)/,
  /^\/api\/auth(\/|$)/,
  /^\/api\/photos(\/|$)/,
];

/** Static API data that is safe to show from a cache, labelled with its last sync time. */
export const CACHEABLE_API: readonly RegExp[] = [/^\/api\/layers\//, /^\/api\/sources(\/|$)/, /^\/api\/regions\//];

export const REALTIME_PATHS: readonly RegExp[] = [/^\/ws(\/|$)/, /^\/sse(\/|$)/];

export function classifyRequest(method: string, pathname: string, mode?: string): RouteClass {
  if (method !== 'GET') return 'network-only';
  if (REALTIME_PATHS.some((r) => r.test(pathname))) return 'network-only';
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    if (NETWORK_ONLY_API.some((r) => r.test(pathname))) return 'network-only';
    if (CACHEABLE_API.some((r) => r.test(pathname))) return 'swr-api';
    return 'network-only'; // anything not explicitly allowlisted is never cached
  }
  if (/\.pmtiles$/i.test(pathname)) return 'pmtiles';
  if (mode === 'navigate') return 'shell-nav';
  if (/^\/(assets|icons)\//.test(pathname) || pathname === '/manifest.webmanifest' || pathname === '/favicon.svg') return 'asset';
  return 'pass';
}

/** Single `bytes=a-b`, `bytes=a-` or `bytes=-n` range against a body of `size` bytes; null when unsatisfiable. */
export function parseRange(header: string | null | undefined, size: number): { start: number; end: number } | null {
  const m = /^bytes=(\d*)-(\d*)$/.exec((header || '').trim());
  if (!m || (m[1] === '' && m[2] === '') || size <= 0) return null;
  let start: number;
  let end: number;
  if (m[1] === '') {
    const n = Number(m[2]);
    if (n === 0) return null;
    start = Math.max(0, size - n);
    end = size - 1;
  } else {
    start = Number(m[1]);
    end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1);
  }
  if (start > end || start >= size) return null;
  return { start, end };
}
