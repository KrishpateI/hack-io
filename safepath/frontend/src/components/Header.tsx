import type { ReactNode } from 'react';
import { SimulatedBadge } from './SimulatedBadge';
import { hasActiveSimulatedIncident, type ActiveIncidentLike } from '../services/labels';

interface HeaderProps {
  /** Overrides the computed SIMULATED indicator. */
  simulatedIndicator?: ReactNode;
  /** Incidents used to show SIMULATED when any active one has simulation_flag true (Req 1.2). */
  incidents?: readonly ActiveIncidentLike[];
}
export function Header({ simulatedIndicator, incidents }: HeaderProps) {
  const indicator = simulatedIndicator ?? (hasActiveSimulatedIncident(incidents) ? <SimulatedBadge /> : null);
  return (
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle bg-surface px-3 py-2">
      <h1 className="text-lg font-semibold text-route">SafePath</h1>
      <div data-testid="simulated-indicator-slot">{indicator}</div>
    </header>
  );
}
