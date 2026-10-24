import type { ReactNode } from 'react';
import { SimulatedBadge } from './SimulatedBadge';
import { StatusIcon } from './StatusIcon';
import { hasActiveSimulatedIncident, type ActiveIncidentLike } from '../services/labels';

export interface HeaderProps {
  /** Overrides the computed SIMULATED indicator. */
  simulatedIndicator?: ReactNode;
  /** Incidents used to show SIMULATED when any active one has simulation_flag true (Req 1.2). */
  incidents?: readonly ActiveIncidentLike[];
  /** Current city; the app covers Chicago only (Req 34.1). */
  city?: string;
  /** Shows the demo-mode indicator (text and icon, never color alone). */
  demoMode?: boolean;
  /** Connectivity status (see ConnectivityStatus). */
  connectivity?: ReactNode;
  /** Profile menu (see ProfileMenu). */
  profile?: ReactNode;
}

function Logo() {
  return (
    <svg aria-hidden="true" focusable="false" width="28" height="28" viewBox="0 0 28 28" className="shrink-0 text-route">
      <path d="M14 2 4 6v7c0 6.2 4.2 10.7 10 13 5.8-2.3 10-6.8 10-13V6L14 2Z" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M8 17c3-1 4-5 6-5s3 3 6 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function Header({ simulatedIndicator, incidents, city = 'Chicago', demoMode = false, connectivity, profile }: HeaderProps) {
  const indicator = simulatedIndicator ?? (hasActiveSimulatedIncident(incidents) ? <SimulatedBadge /> : null);
  return (
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border-subtle bg-surface px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <div className="flex items-center gap-2">
          <Logo />
          <h1 className="text-lg font-semibold text-route">SafePath</h1>
        </div>
        <p data-testid="header-city" className="text-sm text-text-primary">
          <span className="sr-only">Current city: </span>
          {city}
        </p>
        {demoMode && (
          <span
            data-testid="demo-mode-indicator"
            className="inline-flex items-center gap-1 rounded border border-unverified px-2 py-0.5 text-xs font-bold tracking-wide text-unverified"
          >
            <StatusIcon kind="flask" />
            <span>DEMO MODE</span>
          </span>
        )}
        <div data-testid="simulated-indicator-slot">{indicator}</div>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        {connectivity}
        {profile}
      </div>
    </header>
  );
}
