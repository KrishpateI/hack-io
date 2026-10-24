import type { RealtimeEvent } from './eventStore';

export type RealtimeMode = 'appsync' | 'websocket' | 'sse';

export interface RealtimeHandlers {
  onOpen(): void;
  onEvent(event: RealtimeEvent): void;
  /** Connection lost or failed to open. */
  onClose(): void;
}

/** Pluggable transport. connect() must be safe to call again after close(). */
export interface RealtimeClient {
  readonly name: string;
  connect(incidentId: string, handlers: RealtimeHandlers): void;
  close(): void;
}

const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

function parseEvent(data: unknown): RealtimeEvent | null {
  if (typeof data !== 'string') return null;
  try {
    const e = JSON.parse(data) as Partial<RealtimeEvent>;
    if (e && typeof e.event_id === 'string' && typeof e.type === 'string') {
      return { payload: {}, incident_id: '', created_at: '', ...e } as RealtimeEvent;
    }
  } catch {
    /* ignore malformed frames */
  }
  return null;
}

function wsUrl(incidentId: string): string {
  const path = `/ws/incidents/${encodeURIComponent(incidentId)}`;
  const base = API_BASE || (typeof location !== 'undefined' ? location.origin : '');
  return base.replace(/^http/, 'ws') + path;
}

export class WebSocketClient implements RealtimeClient {
  readonly name = 'websocket';
  private ws: WebSocket | null = null;
  connect(incidentId: string, h: RealtimeHandlers): void {
    this.close();
    const ws = new WebSocket(wsUrl(incidentId));
    this.ws = ws;
    ws.onopen = () => h.onOpen();
    ws.onmessage = (m) => {
      const e = parseEvent(m.data);
      if (e) h.onEvent(e);
    };
    ws.onclose = () => {
      if (this.ws === ws) h.onClose();
    };
    ws.onerror = () => ws.close();
  }
  close(): void {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = ws.onmessage = ws.onerror = ws.onopen = null;
      ws.close();
    }
  }
}

export class SseClient implements RealtimeClient {
  readonly name = 'sse';
  private es: EventSource | null = null;
  connect(incidentId: string, h: RealtimeHandlers): void {
    this.close();
    const es = new EventSource(`${API_BASE}/sse/incidents/${encodeURIComponent(incidentId)}`);
    this.es = es;
    es.onopen = () => h.onOpen();
    es.onmessage = (m) => {
      const e = parseEvent(m.data);
      if (e) h.onEvent(e);
    };
    es.onerror = () => {
      // EventSource would auto-retry on its own schedule; we own backoff instead.
      if (this.es === es) {
        es.close();
        h.onClose();
      }
    };
  }
  close(): void {
    const es = this.es;
    this.es = null;
    if (es) {
      es.onopen = es.onmessage = es.onerror = null;
      es.close();
    }
  }
}

/**
 * Placeholder for AWS AppSync Events (Req 25.4: used only if available in the
 * target AWS environment). Not wired yet; it reports closed immediately so the
 * controller falls back to catch-up polling.
 */
export class AppSyncEventsClient implements RealtimeClient {
  readonly name = 'appsync';
  connect(_incidentId: string, h: RealtimeHandlers): void {
    queueMicrotask(() => h.onClose());
  }
  close(): void {}
}

export function createClient(mode: RealtimeMode | undefined = import.meta.env.VITE_REALTIME_MODE): RealtimeClient {
  if (mode === 'sse') return new SseClient();
  if (mode === 'appsync') return new AppSyncEventsClient();
  return new WebSocketClient();
}

/** GET /api/events?since= : events stored after `since`. */
export async function fetchEventsSince(
  incidentId: string,
  since: string | null,
  signal?: AbortSignal,
): Promise<RealtimeEvent[]> {
  const params = new URLSearchParams({ incident_id: incidentId });
  if (since) params.set('since', since);
  const res = await fetch(`${API_BASE}/api/events?${params.toString()}`, { signal });
  if (!res.ok) throw new Error(`Events request failed (${res.status})`);
  const body = (await res.json()) as RealtimeEvent[] | { events: RealtimeEvent[] };
  return Array.isArray(body) ? body : body.events;
}
