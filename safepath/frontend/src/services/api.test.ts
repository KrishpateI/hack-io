import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, demoAct, demoApplyStep, demoRemoveClosure, demoResetAll, demoStart, demoStatus, getJson, listIncidents, listNotices, postJson, presignPhoto, tokenStore } from './api';

type Call = { url: string; init: RequestInit };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(handler: (call: Call) => Response) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => {
    const call = { url, init };
    calls.push(call);
    return handler(call);
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

const auth = (c: Call) => (c.init.headers as Record<string, string>).Authorization;

beforeEach(() => {
  tokenStore.set(null);
  vi.stubEnv('VITE_API_BASE_URL', 'http://api.test');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('api wrapper', () => {
  it('bootstraps a guest session, stores the token and sends it as bearer', async () => {
    const calls = mockFetch((c) =>
      c.url.endsWith('/api/auth/guest') ? json({ token: 'tok1' }) : json([{ id: 'inc_1' }]),
    );
    const out = await listIncidents();
    expect(out).toEqual([{ id: 'inc_1' }]);
    expect(calls[0].url).toBe('http://api.test/api/auth/guest');
    expect(calls[0].init.method).toBe('POST');
    expect(calls[1].url).toBe('http://api.test/api/incidents');
    expect(auth(calls[1])).toBe('Bearer tok1');
    expect(sessionStorage.getItem('safepath.token')).toBe('tok1');
  });

  it('reuses an existing token without bootstrapping', async () => {
    tokenStore.set('existing');
    const calls = mockFetch(() => json({ ok: true }));
    await getJson('/api/health', { a: 1, b: undefined });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('http://api.test/api/health?a=1');
    expect(auth(calls[0])).toBe('Bearer existing');
  });

  it('parses the error envelope into a typed ApiError', async () => {
    tokenStore.set('t');
    mockFetch(() =>
      json(
        { error: { code: 'validation_error', message: 'bad', fields: [{ field: 'x', problem: 'required' }], retry_after_seconds: 7 } },
        422,
      ),
    );
    const err = await postJson('/api/reports', {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const e = err as ApiError;
    expect(e.status).toBe(422);
    expect(e.code).toBe('validation_error');
    expect(e.message).toBe('bad');
    expect(e.fields).toEqual([{ field: 'x', problem: 'required' }]);
    expect(e.retryAfterSeconds).toBe(7);
  });

  it('falls back to a generic ApiError when the body is not an envelope', async () => {
    tokenStore.set('t');
    mockFetch(() => new Response('oops', { status: 502, statusText: 'Bad Gateway' }));
    const e = (await getJson('/api/health').catch((x: unknown) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(502);
    expect(e.code).toBe('http_error');
  });

  it('re-bootstraps a guest session once on 401 and retries the request', async () => {
    tokenStore.set('stale');
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/api/auth/guest')) return json({ token: 'fresh' });
      return auth(c) === 'Bearer fresh' ? json({ ok: 1 }) : json({ error: { code: 'unauthenticated', message: 'no' } }, 401);
    });
    await expect(getJson('/api/incidents')).resolves.toEqual({ ok: 1 });
    expect(calls.map((c) => c.url.replace('http://api.test', ''))).toEqual([
      '/api/incidents',
      '/api/auth/guest',
      '/api/incidents',
    ]);
  });

  it('does not retry more than once when the retry also returns 401', async () => {
    tokenStore.set('stale');
    const calls = mockFetch((c) =>
      c.url.endsWith('/api/auth/guest') ? json({ token: 'fresh' }) : json({ error: { code: 'unauthenticated', message: 'no' } }, 401),
    );
    const e = (await getJson('/api/incidents').catch((x: unknown) => x)) as ApiError;
    expect(e.status).toBe(401);
    expect(calls).toHaveLength(3);
  });
});

describe('notices, photos and demo helpers', () => {
  it('lists notices by incident', async () => {
    const calls = mockFetch((c) => (c.url.endsWith('/api/auth/guest') ? json({ token: 't' }) : json([])));
    await listNotices('inc 1');
    expect(calls[1].url).toBe('http://api.test/api/notices?incident_id=inc+1');
  });
  it('presigns photos and calls demo endpoints with the right method and path', async () => {
    tokenStore.set('t');
    const calls = mockFetch(() => json({ ok: true }));
    await presignPhoto({ content_type: 'image/png', size: 5 });
    await demoStatus();
    await demoStart('s/1');
    await demoApplyStep('s1', 'step 2');
    await demoAct('s1', { actor: 'A', action: 'report', content: 'x' });
    await demoResetAll();
    await demoRemoveClosure('r1', 'i1');
    const seen = calls.map((c) => `${c.init.method} ${c.url.replace('http://api.test', '')}`);
    expect(seen).toEqual([
      'POST /api/photos/presign',
      'GET /api/demo/status',
      'POST /api/demo/scenarios/s%2F1/start',
      'POST /api/demo/scenarios/s1/steps/step%202/apply',
      'POST /api/demo/scenarios/s1/act',
      'POST /api/demo/reset-all',
      'DELETE /api/demo/closures/r1?incident_id=i1',
    ]);
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ content_type: 'image/png', size: 5 });
  });
});
