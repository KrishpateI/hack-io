import { SimulatedBadge } from '../SimulatedBadge';
import { StatusIcon } from '../StatusIcon';
import type { Incident } from '../../services/api';
import { disasterLabel } from './disasters';

/** Boundary info text; the boundary is an incident area, never an official evacuation boundary. */
export function boundarySummary(incident: Incident): string {
  const b = incident.geographic_boundary as { type?: string; coordinates?: unknown } | null | undefined;
  if (!b || typeof b !== 'object' || !b.type) return 'No boundary provided';
  const ring = Array.isArray(b.coordinates) ? (b.coordinates[0] as unknown[] | undefined) : undefined;
  const n = Array.isArray(ring) ? ring.length : 0;
  return n ? `${b.type} boundary (${n} points)` : `${b.type} boundary`;
}

export function ActiveIncident({ incident }: { incident: Incident | null }) {
  return (
    <section aria-labelledby="active-incident-h" data-testid="active-incident" className="p-3">
      <h2 id="active-incident-h" className="mb-2 text-sm font-semibold text-text-primary">Active incident</h2>
      {!incident ? (
        <p className="text-sm text-text-muted">No incident selected.</p>
      ) : (
        <div className="rounded border border-border-subtle bg-surface-raised p-2 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <strong>{disasterLabel(incident.incident_type)}</strong>
            {incident.simulation_flag && <SimulatedBadge />}
          </p>
          <p className="mt-1 flex items-center gap-1">
            <StatusIcon kind={incident.status === 'active' ? 'warning' : 'check'} />
            <span>Status: {incident.status}</span>
          </p>
          <p className="mt-1 text-text-muted">Boundary: {boundarySummary(incident)}</p>
          <p className="mt-1 text-xs text-text-muted">Incident areas are not official evacuation boundaries.</p>
        </div>
      )}
    </section>
  );
}
