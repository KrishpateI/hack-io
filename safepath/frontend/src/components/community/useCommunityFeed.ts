import { useEffect, useMemo, useState } from 'react';
import { listCheckins, listMessages, listReports } from '../../services/api';
import type { EventStoreState } from '../../services/eventStore';
import {
  toCheckIn,
  toMessage,
  toReport,
  type CommunityCheckIn,
  type CommunityMessage,
  type CommunityReport,
} from './types';

type Raw = Record<string, unknown>;
interface Fetched {
  reports: Raw[];
  messages: Raw[];
  checkins: Raw[];
}

const arr = (v: unknown, key: string): Raw[] => {
  const a = (v as Raw | null)?.[key];
  return Array.isArray(a) ? (a as Raw[]) : [];
};

function merge<T>(fetched: Raw[], live: Record<string, Raw>, idKeys: string[], map: (e: Raw) => T | null): T[] {
  const byId = new Map<string, Raw>();
  const idOf = (e: Raw) => idKeys.map((k) => e[k]).find((v) => typeof v === 'string') as string | undefined;
  for (const e of fetched) {
    const id = idOf(e);
    if (id) byId.set(id, e);
  }
  for (const [id, e] of Object.entries(live)) byId.set(id, { ...byId.get(id), ...e });
  return [...byId.values()].map(map).filter((x): x is T => x !== null);
}

/**
 * Initial REST load merged with live realtime-store entities (store fields win, since they
 * are newer). Pass the `store` from `useRealtime(incidentId)`.
 */
export function useCommunityFeed(incidentId: string, store: EventStoreState | undefined) {
  const [fetched, setFetched] = useState<Fetched>({ reports: [], messages: [], checkins: [] });

  useEffect(() => {
    const ctl = new AbortController();
    const q = { incident_id: incidentId };
    void Promise.allSettled([listReports(q, ctl.signal), listMessages(q, ctl.signal), listCheckins(q, ctl.signal)]).then(
      ([r, m, c]) => {
        if (ctl.signal.aborted) return;
        setFetched({
          reports: r.status === 'fulfilled' ? arr(r.value, 'reports') : [],
          messages: m.status === 'fulfilled' ? arr(m.value, 'messages') : [],
          checkins: c.status === 'fulfilled' ? arr(c.value, 'checkins') : [],
        });
      },
    );
    return () => ctl.abort();
  }, [incidentId]);

  const reports = useMemo<CommunityReport[]>(
    () => merge(fetched.reports, store?.reports ?? {}, ['report_id', 'id'], toReport),
    [fetched.reports, store?.reports],
  );
  const messages = useMemo<CommunityMessage[]>(
    () => merge(fetched.messages, store?.messages ?? {}, ['message_id', 'id'], toMessage),
    [fetched.messages, store?.messages],
  );
  const checkins = useMemo<CommunityCheckIn[]>(
    () => merge(fetched.checkins, store?.checkins ?? {}, ['checkin_id', 'check_in_id', 'id'], toCheckIn),
    [fetched.checkins, store?.checkins],
  );
  return { reports, messages, checkins };
}
