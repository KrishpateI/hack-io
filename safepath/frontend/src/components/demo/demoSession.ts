/**
 * Demo session bootstrap (Req 40). SIMULATED only: demo tokens exist when demo mode is on.
 *
 * Two-window demo: open the app a second time with `?demoUser=A` (or B, C). Each browser window
 * keeps its own sessionStorage, so the second window gets its own SIMULATED demo user token and
 * both windows then receive the same realtime events.
 */
import { demoCreateSession, tokenStore } from '../../services/api';

export type DemoActor = 'A' | 'B' | 'C';
export const DEMO_ACTORS: readonly DemoActor[] = ['A', 'B', 'C'];

/** Parse `?demoUser=A|B|C` from a query string; anything else is ignored. */
export function parseDemoUser(search: string): DemoActor | null {
  const v = new URLSearchParams(search).get('demoUser');
  return v === 'A' || v === 'B' || v === 'C' ? v : null;
}

export interface DemoSessionInfo {
  token: string;
  subject: string;
  role: string;
  expiresAt: string | null;
}

export function toSessionInfo(body: Record<string, unknown>): DemoSessionInfo | null {
  const token = typeof body.token === 'string' ? body.token : null;
  if (!token) return null;
  return {
    token,
    subject: typeof body.subject === 'string' ? body.subject : 'unknown',
    role: typeof body.role === 'string' ? body.role : 'unknown',
    expiresAt: typeof body.expires_at === 'string' ? body.expires_at : null,
  };
}

/** Request a demo session and make it this window's session. */
export async function startDemoSession(
  kind: 'guest' | 'user' | 'moderator',
  actor?: DemoActor,
  signal?: AbortSignal,
): Promise<DemoSessionInfo> {
  const body = await demoCreateSession({ kind, actor: actor ?? null }, signal);
  const info = toSessionInfo(body);
  if (!info) throw new Error('Demo session response had no token');
  tokenStore.set(info.token);
  return info;
}

/**
 * Call once at app start (task 13.8 wires it in main.tsx): when the URL carries `?demoUser=X`,
 * obtain that SIMULATED user's token before the app falls back to a guest session. Returns null
 * when the parameter is absent or the request fails (the app then continues as a guest).
 */
export async function bootstrapDemoUser(
  search: string = typeof location === 'undefined' ? '' : location.search,
): Promise<DemoSessionInfo | null> {
  const actor = parseDemoUser(search);
  if (!actor) return null;
  try {
    return await startDemoSession('user', actor);
  } catch {
    return null;
  }
}

/** URL that opens this app as SIMULATED demo user `actor` (a second browser window). */
export function secondWindowUrl(actor: DemoActor, loc: Pick<Location, 'origin' | 'pathname'> = location): string {
  return `${loc.origin}${loc.pathname}?demoUser=${actor}`;
}
