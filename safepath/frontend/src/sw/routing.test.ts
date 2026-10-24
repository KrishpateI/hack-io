import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import swSource from '../../public/sw.js?raw';
import { classifyRequest, parseRange } from './routing';

/** The routing block of public/sw.js, evaluated on its own (it has no dependencies). */
function loadSwRouting() {
  const m = /\/\/ <routing>([\s\S]*?)\/\/ <\/routing>/.exec(swSource);
  if (!m) throw new Error('routing block not found in public/sw.js');
  return new Function(`${m[1]}\nreturn { classifyRequest, parseRange };`)() as {
    classifyRequest: typeof classifyRequest;
    parseRange: typeof parseRange;
  };
}
const sw = loadSwRouting();

const DYNAMIC = [
  '/api/routes/calculate',
  '/api/routes',
  '/api/reports',
  '/api/reports/r1/corroborate',
  '/api/messages',
  '/api/checkins',
  '/api/events',
  '/api/incidents',
  '/api/incidents/i1',
  '/api/notices',
  '/api/demo/status',
  '/api/demo/scenarios/x/start',
  '/api/auth/guest',
  '/api/auth/me',
  '/api/photos/presign',
  '/ws',
  '/ws/incidents/i1',
  '/sse/events',
  '/api/health',
  '/api/something-new',
];

describe('service worker routing (Req 41.1)', () => {
  it.each(DYNAMIC)('%s is network-only (never cached or served stale)', (p) => {
    for (const impl of [classifyRequest, sw.classifyRequest]) {
      expect(impl('GET', p, 'cors')).toBe('network-only');
      expect(impl('GET', p, 'navigate')).toBe('network-only');
    }
  });

  it('never caches non-GET requests', () => {
    for (const p of ['/api/layers/buildings', '/api/sources', '/tiles/chicago.pmtiles', '/']) {
      expect(classifyRequest('POST', p)).toBe('network-only');
      expect(sw.classifyRequest('PUT', p)).toBe('network-only');
    }
  });

  it('caches only the static allowlist and the app shell', () => {
    expect(classifyRequest('GET', '/api/layers/buildings')).toBe('swr-api');
    expect(classifyRequest('GET', '/api/sources')).toBe('swr-api');
    expect(classifyRequest('GET', '/api/regions/chicago/metadata')).toBe('swr-api');
    expect(classifyRequest('GET', '/tiles/chicago.pmtiles')).toBe('pmtiles');
    expect(classifyRequest('GET', '/', 'navigate')).toBe('shell-nav');
    expect(classifyRequest('GET', '/assets/index-abc.js', 'no-cors')).toBe('asset');
    expect(classifyRequest('GET', '/something.json')).toBe('pass');
  });

  it('keeps public/sw.js and src/sw/routing.ts in sync (property)', () => {
    const seg = fc.constantFrom(
      'api', 'routes', 'reports', 'messages', 'checkins', 'events', 'incidents', 'notices', 'demo', 'auth', 'photos',
      'layers', 'sources', 'regions', 'graph', 'ws', 'sse', 'assets', 'icons', 'tiles', 'x.pmtiles', 'chicago.pmtiles',
      'manifest.webmanifest', 'favicon.svg', 'index.html', 'a', '',
    );
    fc.assert(
      fc.property(
        fc.array(seg, { maxLength: 4 }),
        fc.constantFrom('GET', 'POST', 'HEAD', 'DELETE'),
        fc.constantFrom('navigate', 'cors', 'no-cors', undefined),
        (parts, method, mode) => {
          const path = `/${parts.join('/')}`;
          expect(sw.classifyRequest(method, path, mode)).toBe(classifyRequest(method, path, mode));
        },
      ),
      { numRuns: 300 },
    );
  });

  it('property: nothing under a dynamic API prefix is ever cacheable', () => {
    const prefixes = ['routes', 'reports', 'messages', 'checkins', 'events', 'incidents', 'notices', 'demo', 'auth'];
    fc.assert(
      fc.property(fc.constantFrom(...prefixes), fc.stringMatching(/^[a-z0-9/_-]{0,20}$/), (p, rest) => {
        const path = `/api/${p}${rest ? `/${rest}` : ''}`;
        for (const impl of [classifyRequest, sw.classifyRequest]) expect(impl('GET', path, 'cors')).toBe('network-only');
      }),
    );
  });
});

describe('Range parsing for the cached PMTiles file', () => {
  it('handles closed, open and suffix ranges', () => {
    for (const impl of [parseRange, sw.parseRange]) {
      expect(impl('bytes=0-15', 100)).toEqual({ start: 0, end: 15 });
      expect(impl('bytes=90-', 100)).toEqual({ start: 90, end: 99 });
      expect(impl('bytes=-10', 100)).toEqual({ start: 90, end: 99 });
      expect(impl('bytes=50-500', 100)).toEqual({ start: 50, end: 99 });
      expect(impl('bytes=100-120', 100)).toBeNull();
      expect(impl('bytes=5-2', 100)).toBeNull();
      expect(impl('items=0-1', 100)).toBeNull();
      expect(impl(null, 100)).toBeNull();
    }
  });

  it('property: a satisfiable range lies inside the body and both implementations agree', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 5000 }), fc.nat(6000), fc.nat(6000), (size, a, b) => {
        const h = `bytes=${a}-${b}`;
        const r = parseRange(h, size);
        expect(sw.parseRange(h, size)).toEqual(r);
        if (r) {
          expect(r.start).toBeGreaterThanOrEqual(0);
          expect(r.end).toBeLessThan(size);
          expect(r.start).toBeLessThanOrEqual(r.end);
        }
      }),
    );
  });
});
