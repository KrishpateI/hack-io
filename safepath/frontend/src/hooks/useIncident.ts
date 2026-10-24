import { useEffect, useState } from 'react';
import { listIncidents, type Incident } from '../services/api';

/** Loads incidents (GET /api/incidents) and tracks the selected one. */
export function useIncident() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Incident | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    listIncidents(ac.signal).then(
      (list) => { setIncidents(list); setLoading(false); },
      (e: unknown) => { if (!ac.signal.aborted) { setError(e instanceof Error ? e.message : String(e)); setLoading(false); } },
    );
    return () => ac.abort();
  }, []);
  return { incidents, loading, error, selected, select: setSelected };
}
