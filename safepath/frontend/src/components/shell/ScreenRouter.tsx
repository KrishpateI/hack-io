import type { ComponentProps } from 'react';
import { CommunityCommunicationsScreen } from '../../pages/CommunityCommunicationsScreen';
import { DataSourcesScreen } from '../../pages/DataSourcesScreen';
import { DisasterSelectionScreen } from '../../pages/DisasterSelectionScreen';
import { IncidentOverviewScreen } from '../../pages/IncidentOverviewScreen';
import { OfficialGuidanceScreen } from '../../pages/OfficialGuidanceScreen';
import { RouteDetailsScreen } from '../../pages/RouteDetailsScreen';
import { SafetyCheckInScreen } from '../../pages/SafetyCheckInScreen';
import { SubmitHazardReportScreen } from '../../pages/SubmitHazardReportScreen';
import type { Incident } from '../../services/api';
import type { EventStoreState } from '../../services/eventStore';
import { toNotice } from '../community/types';
import { useCommunityFeed } from '../community/useCommunityFeed';
import type { RoutePanelProps } from '../route/RoutePanel';
import type { IncidentType } from '../sidebar/disasters';
import { ScreenFrame } from './ScreenFrame';
import { screenTitle, type ScreenId } from './screens';

export interface ScreenRouterProps {
  screen: ScreenId;
  onClose: () => void;
  incident: Incident | null;
  incidents: Incident[];
  incidentsLoading: boolean;
  incidentsError: string | null;
  disasterType: IncidentType | null;
  store: EventStoreState | undefined;
  routePanel: RoutePanelProps;
  activeRestrictionCount: number;
  location: { latitude: number; longitude: number } | null;
  indoorFeatureId: string | null;
  onClearLocation: () => void;
  onSelectType: (t: IncidentType) => void;
  onSelectIncident: (i: Incident) => void;
}

/** Needs an incident id; community data is incident-scoped (Req 23, 27). */
function NeedsIncident({ screen, onClose }: { screen: ScreenId; onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle(screen)} onClose={onClose} testId={`screen-${screen}`}>
      <p role="status" className="rounded border border-border-subtle p-3 text-sm">
        Select an incident first. This screen shows information for one incident.
      </p>
    </ScreenFrame>
  );
}

function CommunityContainer({ incidentId, store, onClose }: { incidentId: string; store: EventStoreState | undefined; onClose: () => void }) {
  const feed = useCommunityFeed(incidentId, store);
  return <CommunityCommunicationsScreen incidentId={incidentId} feed={feed} onClose={onClose} />;
}

function OverviewContainer({
  incident, store, restrictions, onClose,
}: { incident: Incident; store: EventStoreState | undefined; restrictions: number; onClose: () => void }) {
  const feed = useCommunityFeed(incident.incident_id, store);
  const notices = feed.messages.filter((m) => m.is_official).map(toNotice);
  return (
    <IncidentOverviewScreen
      incident={incident}
      reports={feed.reports}
      notices={notices}
      activeRestrictionCount={restrictions}
      onClose={onClose}
    />
  );
}

function CheckInContainer({
  incidentId, store, location, onClose,
}: { incidentId: string; store: EventStoreState | undefined; location: ScreenRouterProps['location']; onClose: () => void }) {
  const feed = useCommunityFeed(incidentId, store);
  return <SafetyCheckInScreen incidentId={incidentId} location={location} checkins={feed.checkins} onClose={onClose} />;
}

export function ScreenRouter(p: ScreenRouterProps) {
  const id = p.incident?.incident_id ?? null;
  switch (p.screen) {
    case 'disaster-selection':
      return (
        <DisasterSelectionScreen
          selectedType={p.disasterType}
          incidents={p.incidents}
          selectedIncidentId={id}
          loading={p.incidentsLoading}
          error={p.incidentsError}
          onSelectType={p.onSelectType}
          onSelectIncident={p.onSelectIncident}
          onClose={p.onClose}
        />
      );
    case 'route-details':
      return <RouteDetailsScreen {...(p.routePanel as ComponentProps<typeof RouteDetailsScreen>)} onClose={p.onClose} />;
    case 'submit-report':
      return id ? (
        <SubmitHazardReportScreen
          incidentId={id}
          location={p.location}
          indoorFeatureId={p.indoorFeatureId}
          onClearLocation={p.onClearLocation}
          onClose={p.onClose}
        />
      ) : (
        <NeedsIncident screen={p.screen} onClose={p.onClose} />
      );
    case 'community':
      return id ? <CommunityContainer incidentId={id} store={p.store} onClose={p.onClose} /> : <NeedsIncident screen={p.screen} onClose={p.onClose} />;
    case 'incident-overview':
      return p.incident ? (
        <OverviewContainer incident={p.incident} store={p.store} restrictions={p.activeRestrictionCount} onClose={p.onClose} />
      ) : (
        <IncidentOverviewScreen incident={null} reports={[]} notices={[]} activeRestrictionCount={0} onClose={p.onClose} />
      );
    case 'safety-check-in':
      return id ? (
        <CheckInContainer incidentId={id} store={p.store} location={p.location} onClose={p.onClose} />
      ) : (
        <NeedsIncident screen={p.screen} onClose={p.onClose} />
      );
    case 'official-guidance':
      return <OfficialGuidanceScreen hazard={p.disasterType} onClose={p.onClose} />;
    case 'data-sources':
      return <DataSourcesScreen onClose={p.onClose} />;
  }
}
