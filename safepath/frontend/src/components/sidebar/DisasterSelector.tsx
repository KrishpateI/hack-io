import { SimulatedBadge } from '../SimulatedBadge';
import type { Incident } from '../../services/api';
import { DISASTERS, disasterLabel, type IncidentType } from './disasters';

interface Props {
  selectedType: IncidentType | null;
  incidents: Incident[];
  selectedIncidentId: string | null;
  loading?: boolean;
  error?: string | null;
  onSelectType: (t: IncidentType) => void;
  onSelectIncident: (incident: Incident) => void;
}

export function DisasterSelector({ selectedType, incidents, selectedIncidentId, loading, error, onSelectType, onSelectIncident }: Props) {
  const info = DISASTERS.find((d) => d.type === selectedType);
  const matching = incidents.filter((i) => i.incident_type === selectedType);
  return (
    <section aria-labelledby="disaster-h" data-testid="disaster-selector" className="p-3">
      <h2 id="disaster-h" className="mb-2 text-sm font-semibold text-text-primary">Disaster type</h2>
      <div role="group" aria-labelledby="disaster-h" className="flex flex-wrap gap-2">
        {DISASTERS.map((d) => (
          <button
            key={d.type}
            type="button"
            aria-pressed={selectedType === d.type}
            onClick={() => onSelectType(d.type)}
            className={`min-h-11 rounded border px-3 py-1 text-sm focus:outline-2 focus:outline-route ${
              selectedType === d.type ? 'border-route bg-surface-raised font-semibold text-route' : 'border-border-subtle text-text-primary'
            }`}
          >
            {selectedType === d.type ? '\u2713 ' : ''}{d.label}
          </button>
        ))}
      </div>
      {info && (
        <p data-testid="disaster-description" className="mt-2 text-sm text-text-muted">
          <strong className="text-text-primary">How routing changes: </strong>{info.routing}
        </p>
      )}
      {selectedType && (
        <div className="mt-2">
          <h3 className="text-sm font-medium">{disasterLabel(selectedType)} incidents</h3>
          {loading && <p role="status" className="text-sm text-text-muted">Loading incidents…</p>}
          {error && <p role="alert" className="text-sm text-closure">Could not load incidents: {error}</p>}
          {!loading && !error && matching.length === 0 && <p className="text-sm text-text-muted">No incidents of this type.</p>}
          <ul className="mt-1 space-y-1">
            {matching.map((i) => (
              <li key={i.incident_id}>
                <button
                  type="button"
                  aria-pressed={selectedIncidentId === i.incident_id}
                  onClick={() => onSelectIncident(i)}
                  className="flex min-h-11 w-full flex-wrap items-center gap-2 rounded border border-border-subtle px-2 py-1 text-left text-sm focus:outline-2 focus:outline-route"
                >
                  <span>{selectedIncidentId === i.incident_id ? '\u2713 ' : ''}{i.incident_id}</span>
                  <span className="text-text-muted">({i.status})</span>
                  {i.simulation_flag && (
                    <>
                      <SimulatedBadge />
                      <span className="text-xs text-route-alt">SIMULATED scenario</span>
                    </>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
