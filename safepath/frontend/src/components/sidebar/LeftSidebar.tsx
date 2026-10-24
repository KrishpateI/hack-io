import { useState } from 'react';
import type { Incident, RouteResponse } from '../../services/api';
import { OfficialLinks } from '../OfficialLinks';
import { ActiveIncident } from './ActiveIncident';
import { DisasterSelector } from './DisasterSelector';
import type { IncidentType } from './disasters';
import { OfficialGuidance } from './OfficialGuidance';
import { RouteUncertaintySummary } from './RouteUncertaintySummary';
import { StartDestinationPicker, type Destination, type LatLng } from './StartDestinationPicker';

export interface LeftSidebarProps {
  incident: Incident | null;
  incidents: Incident[];
  incidentsLoading?: boolean;
  incidentsError?: string | null;
  disasterType: IncidentType | null;
  start: LatLng | null;
  destination: Destination | null;
  pickMode: 'start' | 'destination' | null;
  routeResponse: RouteResponse | null;
  onSelectType: (t: IncidentType) => void;
  onSelectIncident: (i: Incident) => void;
  onStartChange: (p: LatLng) => void;
  onDestinationChange: (p: Destination) => void;
  onPickModeChange: (m: 'start' | 'destination' | null) => void;
}

/** Left sidebar (Req 34.2). Collapsible below the md breakpoint so it fits 320 px screens. */
export function LeftSidebar(p: LeftSidebarProps) {
  const [open, setOpen] = useState(true);
  return (
    <aside aria-label="Incident and route setup" data-testid="left-sidebar" className="w-full bg-surface text-text-primary md:w-80">
      <button type="button" aria-expanded={open} aria-controls="left-sidebar-body" onClick={() => setOpen((o) => !o)}
        className="min-h-11 w-full border-b border-border-subtle px-3 text-left text-sm font-semibold focus:outline-2 focus:outline-route md:hidden">
        {open ? 'Hide' : 'Show'} incident and route setup
      </button>
      <div id="left-sidebar-body" hidden={!open} className="divide-y divide-border-subtle overflow-y-auto md:block! md:max-h-full">
        <ActiveIncident incident={p.incident} />
        <DisasterSelector selectedType={p.disasterType} incidents={p.incidents} selectedIncidentId={p.incident?.incident_id ?? null}
          loading={p.incidentsLoading} error={p.incidentsError} onSelectType={p.onSelectType} onSelectIncident={p.onSelectIncident} />
        <StartDestinationPicker start={p.start} destination={p.destination} pickMode={p.pickMode} onPickModeChange={p.onPickModeChange}
          onStartChange={p.onStartChange} onDestinationChange={p.onDestinationChange} showSamplePoints={p.disasterType === 'flood'} />
        <RouteUncertaintySummary response={p.routeResponse} />
        <OfficialLinks />
        <OfficialGuidance hazard={p.disasterType} />
      </div>
    </aside>
  );
}
