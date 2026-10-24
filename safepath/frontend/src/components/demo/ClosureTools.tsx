import { useState } from 'react';
import { demoAddClosure, demoRemoveClosure } from '../../services/api';
import { StatusIcon } from '../StatusIcon';
import { describeError } from './demoModel';

interface Added {
  restrictionId: string;
  incidentId: string;
}

/** Ad-hoc SIMULATED closure add/remove for a demo incident. Not a real road closure. */
export function ClosureTools({ incidentIds }: { incidentIds: string[] }) {
  const [incident, setIncident] = useState('');
  const [edges, setEdges] = useState('');
  const [kind, setKind] = useState<'closure' | 'impassable_polygon'>('closure');
  const [polygon, setPolygon] = useState('');
  const [added, setAdded] = useState<Added[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const incidentId = incident || incidentIds[0] || '';
  const input = 'min-h-11 w-full rounded border border-border-subtle bg-surface px-2 py-1 text-sm';

  const add = async () => {
    const edge_ids = kind === 'closure' ? edges.split(/[\s,]+/).filter(Boolean) : [];
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const geometry = kind === 'impassable_polygon' ? (JSON.parse(polygon) as Record<string, unknown>) : null;
      const res = await demoAddClosure({ incident_id: incidentId, edge_ids, geometry, kind });
      const rid = typeof res.restriction_id === 'string' ? res.restriction_id : '';
      setAdded((a) => [...a, { restrictionId: rid, incidentId }]);
      setMessage(`Added SIMULATED closure ${rid} to ${incidentId}. Demo only, not a real closure.`);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (a: Added) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await demoRemoveClosure(a.restrictionId, a.incidentId);
      setAdded((list) => list.filter((x) => x !== a));
      setMessage(`Removed SIMULATED closure ${a.restrictionId}.`);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="closure-h" className="space-y-2 rounded border border-border-subtle p-3" data-testid="closure-tools">
      <h3 id="closure-h" className="text-lg font-semibold">Ad-hoc SIMULATED closures</h3>
      <p className="text-sm text-text-muted">Demo only, not real events. Routes avoid the closure as soon as it is added.</p>
      <label className="block text-sm">
        <span>Incident</span>
        <select className={input} value={incidentId} onChange={(e) => setIncident(e.target.value)}>
          {incidentIds.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        <span>Kind</span>
        <select className={input} value={kind} onChange={(e) => setKind(e.target.value as 'closure' | 'impassable_polygon')}>
          <option value="closure">Road closure (edge ids)</option>
          <option value="impassable_polygon">Hazard polygon (GeoJSON geometry)</option>
        </select>
      </label>
      {kind === 'closure' ? (
        <label className="block text-sm">
          <span>Edge ids (comma or space separated)</span>
          <input className={input} value={edges} onChange={(e) => setEdges(e.target.value)} />
        </label>
      ) : (
        <label className="block text-sm">
          <span>Polygon geometry (GeoJSON)</span>
          <textarea className={input} rows={4} value={polygon} onChange={(e) => setPolygon(e.target.value)} />
        </label>
      )}
      <button
        type="button"
        className="min-h-11 rounded border border-border-subtle bg-surface-raised px-3 text-sm"
        onClick={add}
        disabled={busy || !incidentId || (kind === 'closure' ? edges.trim() === '' : polygon.trim() === '')}
      >
        Add SIMULATED {kind === 'closure' ? 'closure' : 'hazard polygon'}
      </button>
      {added.length > 0 && (
        <ul className="space-y-1">
          {added.map((a) => (
            <li key={a.restrictionId} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="break-all">{a.restrictionId} ({a.incidentId})</span>
              <button
                type="button"
                className="min-h-11 rounded border border-border-subtle px-3 text-sm"
                onClick={() => remove(a)}
                disabled={busy}
                aria-label={`Remove closure ${a.restrictionId}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {message && <p role="status" className="text-sm"><StatusIcon kind="check" /> {message}</p>}
      {error && <p role="alert" className="rounded border border-closure p-2 text-sm"><StatusIcon kind="cross" /> {error}</p>}
    </section>
  );
}
