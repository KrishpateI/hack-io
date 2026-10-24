import { useEffect, useState } from 'react';
import { HistoricalLabel } from '../components/HistoricalLabel';
import { SourceLabel } from '../components/SourceLabel';
import { SourceRecordPopup } from '../components/SourceRecordPopup';
import { LIMITATIONS } from '../services/limitations';
import { fetchSources, type SourceFilters, type SourceRecord } from '../services/sources';

type Result = { key: string; error?: string; records?: SourceRecord[] };

export default function SourcesLimitations() {
  const [filters, setFilters] = useState<SourceFilters>({});
  const [result, setResult] = useState<Result | null>(null);
  const [selected, setSelected] = useState<SourceRecord | null>(null);
  const [attempt, setAttempt] = useState(0);

  const key = JSON.stringify([filters, attempt]);
  useEffect(() => {
    const ctl = new AbortController();
    fetchSources(filters, ctl.signal)
      .then((records) => setResult({ key, records }))
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setResult({ key, error: e instanceof Error ? e.message : 'Unknown error' });
      });
    return () => ctl.abort();
  }, [filters, attempt, key]);
  const load: { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; records: SourceRecord[] } =
    !result || result.key !== key ? { state: 'loading' } : result.error !== undefined ? { state: 'error', message: result.error } : { state: 'ready', records: result.records ?? [] };

  const set = (k: keyof SourceFilters) => (e: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [k]: e.target.value }));
  const input = 'rounded border border-border-subtle bg-surface px-2 py-1 text-sm';

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-3">
      <h2 className="text-xl font-semibold">Data sources and limitations</h2>

      <section aria-labelledby="src-h" className="space-y-3">
        <h3 id="src-h" className="text-lg font-semibold">Data sources</h3>
        <div className="flex flex-wrap gap-2">
          <label className="text-sm">
            <span className="mr-1">Search</span>
            <input type="search" className={input} value={filters.q ?? ''} onChange={set('q')} />
          </label>
          <label className="text-sm">
            <span className="mr-1">Classification</span>
            <select className={input} value={filters.data_classification ?? ''} onChange={set('data_classification')}>
              <option value="">All</option>
              {['Historical', 'Static', 'Current', 'Simulated'].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mr-1">Verification</span>
            <select className={input} value={filters.verification_status ?? ''} onChange={set('verification_status')}>
              <option value="">All</option>
              {['verified', 'unverified', 'unknown', 'simulated'].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>

        {load.state === 'loading' && <p role="status">Loading sources...</p>}
        {load.state === 'error' && (
          <div role="alert" className="rounded border border-closure p-2 text-sm">
            <p>Source list unavailable ({load.message}). The documented limitations below remain available offline.</p>
            <button type="button" className="mt-1 underline" onClick={() => setAttempt((n) => n + 1)}>
              Retry
            </button>
          </div>
        )}
        {load.state === 'ready' && load.records.length === 0 && <p>No sources match these filters.</p>}
        {load.state === 'ready' && load.records.length > 0 && (
          <ul className="space-y-2" data-testid="source-list">
            {load.records.map((r) => (
              <li key={r.source_id} className="rounded border border-border-subtle p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <SourceLabel item={{ source_record: { is_official: r.is_official_source } }} />
                  {r.data_classification === 'Historical' ? <HistoricalLabel /> : null}
                  <button type="button" className="text-left text-route underline" onClick={() => setSelected(r)}>
                    {r.title}
                  </button>
                </div>
                <p className="text-xs text-text-muted">
                  {r.organization} | {r.data_classification} | {r.verification_status}
                </p>
              </li>
            ))}
          </ul>
        )}
        {selected && <SourceRecordPopup record={selected} onClose={() => setSelected(null)} />}
      </section>

      <section aria-labelledby="lim-h" className="space-y-2">
        <h3 id="lim-h" className="text-lg font-semibold">Limitations</h3>
        <ul className="list-disc space-y-1 pl-5" data-testid="limitations-list">
          {LIMITATIONS.map((l) => (
            <li key={l.id}>
              <strong>{l.title}.</strong> {l.detail}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

