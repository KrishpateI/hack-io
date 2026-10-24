/* SafePath service worker (hand-written, no runtime dependencies).
 *
 * Caches: the app shell (precached, versioned), the PMTiles basemap (one opt-in full download,
 * then Range-aware answers), and a small allowlist of static API data (layers, sources, region
 * metadata) as stale-while-revalidate.
 *
 * NEVER cached or answered stale: routes, reports, messages, check-ins, events, incidents,
 * notices, demo, auth, photos, WebSocket and SSE. Those go straight to the network, so when the
 * device is offline they fail and the UI says so.
 *
 * The two placeholders below are filled in by the build (vite.config.ts). In dev they keep their
 * defaults. Routing rules are mirrored in src/sw/routing.ts and kept in sync by routing.test.ts.
 */
const BUILD_ID = 'dev' /*__BUILD_ID__*/;
const PRECACHE = ['/', '/index.html'] /*__PRECACHE__*/;

const SHELL_CACHE = 'safepath-shell-' + BUILD_ID;
const API_CACHE = 'safepath-api-v1';
const TILES_CACHE = 'safepath-tiles-v1';
const KEEP = [SHELL_CACHE, API_CACHE, TILES_CACHE];

// <routing>
const NETWORK_ONLY_API = [
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
const CACHEABLE_API = [/^\/api\/layers\//, /^\/api\/sources(\/|$)/, /^\/api\/regions\//];
const REALTIME_PATHS = [/^\/ws(\/|$)/, /^\/sse(\/|$)/];

function classifyRequest(method, pathname, mode) {
  if (method !== 'GET') return 'network-only';
  if (REALTIME_PATHS.some((r) => r.test(pathname))) return 'network-only';
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    if (NETWORK_ONLY_API.some((r) => r.test(pathname))) return 'network-only';
    if (CACHEABLE_API.some((r) => r.test(pathname))) return 'swr-api';
    return 'network-only';
  }
  if (/\.pmtiles$/i.test(pathname)) return 'pmtiles';
  if (mode === 'navigate') return 'shell-nav';
  if (/^\/(assets|icons)\//.test(pathname) || pathname === '/manifest.webmanifest' || pathname === '/favicon.svg') return 'asset';
  return 'pass';
}

function parseRange(header, size) {
  const m = /^bytes=(\d*)-(\d*)$/.exec((header || '').trim());
  if (!m || (m[1] === '' && m[2] === '') || size <= 0) return null;
  let start;
  let end;
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
// </routing>

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((n) => n.startsWith('safepath-') && !KEEP.includes(n)).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  const cls = classifyRequest(req.method, url.pathname, req.mode);
  const sameOrigin = url.origin === self.location.origin;
  // Network-only and unknown requests are not intercepted: no cache read, no cache write.
  if (cls === 'network-only' || cls === 'pass') return;
  if (cls === 'pmtiles') return void event.respondWith(handlePmtiles(req));
  if (cls === 'swr-api') return void event.respondWith(handleStaticApi(event, req));
  if (!sameOrigin) return;
  if (cls === 'shell-nav') return void event.respondWith(handleNavigation(req));
  if (cls === 'asset') return void event.respondWith(handleAsset(req));
});

async function handleNavigation(req) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) await cache.put('/index.html', res.clone());
    return res;
  } catch (err) {
    const cached = (await cache.match('/index.html')) || (await cache.match('/'));
    if (cached) return cached;
    throw err;
  }
}

async function handleAsset(req) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) await cache.put(req, res.clone());
  return res;
}

/** Re-wrap a response with extra headers (bodies of cached responses are immutable). */
async function withHeaders(res, extra) {
  const headers = new Headers(res.headers);
  for (const k of Object.keys(extra)) headers.set(k, extra[k]);
  return new Response(await res.blob(), { status: res.status, statusText: res.statusText, headers });
}

/**
 * Static API data: answer from cache and revalidate in the background. A cached answer carries
 * `x-sp-source: cache` and `x-sp-cached-at` so the UI can label it "Cached map data".
 */
async function handleStaticApi(event, req) {
  const cache = await caches.open(API_CACHE);
  const key = req.url;
  const hit = await cache.match(key);
  const refresh = fetch(req)
    .then(async (res) => {
      if (res.ok) await cache.put(key, await withHeaders(res.clone(), { 'x-sp-cached-at': new Date().toISOString() }));
      return res;
    });
  if (hit) {
    event.waitUntil(refresh.catch(() => undefined));
    return withHeaders(hit, { 'x-sp-source': 'cache' });
  }
  return refresh;
}

async function handlePmtiles(req) {
  const cache = await caches.open(TILES_CACHE);
  const full = await cache.match(req.url);
  if (!full) return fetch(req); // not downloaded: normal network Range request, nothing stored
  const range = req.headers.get('range');
  if (!range) return full;
  const blob = await full.blob();
  const r = parseRange(range, blob.size);
  if (!r) return new Response(null, { status: 416, headers: { 'Content-Range': 'bytes */' + blob.size } });
  const part = blob.slice(r.start, r.end + 1);
  return new Response(part, {
    status: 206,
    headers: {
      'Content-Type': full.headers.get('Content-Type') || 'application/octet-stream',
      'Content-Range': 'bytes ' + r.start + '-' + r.end + '/' + blob.size,
      'Content-Length': String(part.size),
      'Accept-Ranges': 'bytes',
      'x-sp-source': 'cache',
    },
  });
}

// ---- opt-in basemap download (one full fetch, stored whole so ranges can be served offline)
async function postAll(msg) {
  for (const c of await self.clients.matchAll({ includeUncontrolled: true })) c.postMessage(msg);
}

async function downloadPmtiles(url) {
  const cache = await caches.open(TILES_CACHE);
  await postAll({ type: 'PMTILES_STATUS', state: 'downloading', url });
  try {
    const res = await fetch(url, { mode: 'cors' });
    if (res.status !== 200) throw new Error('HTTP ' + res.status);
    const copy = await withHeaders(res, { 'x-sp-cached-at': new Date().toISOString() });
    await cache.put(url, copy);
    await postAll({ type: 'PMTILES_STATUS', state: 'cached', url });
  } catch (err) {
    await postAll({ type: 'PMTILES_STATUS', state: 'error', url, message: String((err && err.message) || err) });
  }
}

self.addEventListener('message', (event) => {
  const d = event.data || {};
  if (typeof d.url === 'string' && !/\.pmtiles$/i.test(new URL(d.url, self.location.origin).pathname)) return;
  if (d.type === 'CACHE_PMTILES' && typeof d.url === 'string') {
    event.waitUntil(downloadPmtiles(new URL(d.url, self.location.origin).href));
  } else if (d.type === 'PMTILES_STATUS_QUERY' && typeof d.url === 'string') {
    const href = new URL(d.url, self.location.origin).href;
    event.waitUntil(
      caches
        .open(TILES_CACHE)
        .then((c) => c.match(href))
        .then((hit) => postAll({ type: 'PMTILES_STATUS', state: hit ? 'cached' : 'none', url: href })),
    );
  }
});
