import { useEffect, useState } from 'react';
import { fetchSources, type SourceRecord } from '../../services/sources';

let cache: Promise<SourceRecord[]> | null = null;

/**
 * Loads the Source_Record catalog once so feature popups can render from memory (Req 10.3: within
 * 1 s of a click). Failures are not cached; the inspector then shows "Unknown source".
 */
export function useSourceRecords(load: () => Promise<SourceRecord[]> = fetchSources): SourceRecord[] {
  const [records, setRecords] = useState<SourceRecord[]>([]);
  useEffect(() => {
    let live = true;
    const p = (cache ??= load());
    p.then((r) => live && setRecords(r)).catch(() => {
      cache = null;
    });
    return () => {
      live = false;
    };
  }, [load]);
  return records;
}

export function resetSourceRecordCache(): void {
  cache = null;
}
