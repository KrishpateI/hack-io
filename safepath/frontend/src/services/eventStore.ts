/** Event envelope delivered by the backend realtime channel (task 9.1). */
export interface RealtimeEvent {
  event_id: string;
  type: string;
  incident_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type EntityKind = 'reports' | 'messages' | 'checkins' | 'restrictions';
export type Entity = Record<string, unknown>;

export interface EventStoreState {
  reports: Record<string, Entity>;
  messages: Record<string, Entity>;
  checkins: Record<string, Entity>;
  restrictions: Record<string, Entity>;
  appliedEventIds: Record<string, true>;
  /** Greatest event_id applied (event ids sort chronologically). */
  lastEventId: string | null;
  /**
   * Per-field versions, keyed by `${kind}:${entityId}` then field name: the event_id that
   * last wrote each field. Kept outside the entities so consumers only see payload fields.
   */
  fieldVersions: Record<string, Record<string, string>>;
}

export const initialEventState: EventStoreState = {
  reports: {},
  messages: {},
  checkins: {},
  restrictions: {},
  appliedEventIds: {},
  lastEventId: null,
  fieldVersions: {},
};

/** Map an event type such as "report.created" or "check_in.updated" to an entity bucket. */
export function kindOf(type: string): EntityKind | null {
  const t = type.toLowerCase();
  if (t.startsWith('report')) return 'reports';
  if (t.startsWith('message')) return 'messages';
  if (t.startsWith('check')) return 'checkins';
  if (t.startsWith('restriction')) return 'restrictions';
  return null;
}

const ID_KEYS: Record<EntityKind, string[]> = {
  reports: ['report_id', 'id'],
  messages: ['message_id', 'id'],
  checkins: ['checkin_id', 'check_in_id', 'id'],
  restrictions: ['restriction_id', 'id'],
};

function entityId(kind: EntityKind, payload: Entity): string | null {
  for (const k of ID_KEYS[kind]) {
    const v = payload[k];
    if (typeof v === 'string' && v) return v;
  }
  return null;
}

/**
 * Apply one event. Idempotent: an already-applied event_id returns the same
 * state object. Entities are merged field-wise and each field carries its own
 * version: a field is only overwritten by an event whose id is newer than the
 * event that last wrote that field. The result therefore equals applying every
 * distinct event once in event_id order, whatever the arrival order.
 * `_event_id` / `_updated_at` always reflect the newest event for the entity.
 */
export function applyEvent(state: EventStoreState, event: RealtimeEvent): EventStoreState {
  if (state.appliedEventIds[event.event_id]) return state;
  const next: EventStoreState = {
    ...state,
    appliedEventIds: { ...state.appliedEventIds, [event.event_id]: true },
    lastEventId:
      state.lastEventId === null || event.event_id > state.lastEventId ? event.event_id : state.lastEventId,
  };
  const kind = kindOf(event.type);
  if (!kind) return next;
  const id = entityId(kind, event.payload ?? {});
  if (!id) return next;
  const bucket = state[kind];
  const existing = bucket[id];
  const versionKey = `${kind}:${id}`;
  const versions = state.fieldVersions[versionKey] ?? {};
  const merged: Entity = { ...existing };
  const nextVersions: Record<string, string> = { ...versions };
  for (const [field, value] of Object.entries(event.payload ?? {})) {
    const fieldVersion = versions[field];
    if (fieldVersion === undefined || event.event_id > fieldVersion) {
      merged[field] = value;
      nextVersions[field] = event.event_id;
    }
  }
  const entityVersion = typeof existing?._event_id === 'string' ? existing._event_id : '';
  if (event.event_id > entityVersion) {
    merged._event_id = event.event_id;
    merged._updated_at = event.created_at;
  }
  return {
    ...next,
    [kind]: { ...bucket, [id]: merged },
    fieldVersions: { ...state.fieldVersions, [versionKey]: nextVersions },
  };
}

/** Apply a batch in event_id order. Duplicates are ignored. */
export function applyEvents(state: EventStoreState, events: RealtimeEvent[]): EventStoreState {
  const sorted = [...events].sort((a, b) => (a.event_id < b.event_id ? -1 : a.event_id > b.event_id ? 1 : 0));
  return sorted.reduce(applyEvent, state);
}
