import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { fetchIndoorBuilding } from './components/indoor/indoorService';
import type { IndoorGeoJson } from './components/indoor/indoorModel';
import { FeatureInspector, useFeatureInspector } from './components/inspect';
import type { MapClick } from './components/inspect/inspectModel';
import type { EdgeReport } from './components/inspect/edgeService';
import { useViewportTier } from './components/ios/hooks';
import { RootLayout } from './components/RootLayout';
import type { RoutePanelProps } from './components/route/RoutePanel';
import { AppFrame } from './components/shell/AppFrame';
import { ConnectivityStatus, type ConnectivityState } from './components/shell/ConnectivityStatus';
import { ErrorBoundary } from './components/shell/ErrorBoundary';
import { ProfileMenu } from './components/shell/ProfileMenu';
import { ScreenFrame } from './components/shell/ScreenFrame';
import { ScreenRouter } from './components/shell/ScreenRouter';
import { CommunityTab } from './components/shell/tabs/CommunityTab';
import { MapTab } from './components/shell/tabs/MapTab';
import { MoreTab } from './components/shell/tabs/MoreTab';
import { RouteTab, type TravelMode } from './components/shell/tabs/RouteTab';
import { SafetyTab } from './components/shell/tabs/SafetyTab';
import { TabHeading } from './components/shell/TabHeading';
import { useKeyboardShortcuts } from './components/shell/useKeyboardShortcuts';
import { useNavigation } from './components/shell/useNavigation';
import type { TabId } from './components/shell/navigation';
import { useCommunityFeed } from './components/community/useCommunityFeed';
import { readShortcuts, writeShortcuts } from './components/ios/preferences';
import type { IncidentType } from './components/sidebar/disasters';
import type { Destination, LatLng } from './components/sidebar/StartDestinationPicker';
import { useActiveRestrictions, useEdgeCoordinates } from './hooks/useActiveRestrictions';
import { useActiveRoute } from './hooks/useActiveRoute';
import { useAuth } from './hooks/useAuth';
import { useIncident } from './hooks/useIncident';
import { useMapOverlays } from './hooks/useMapOverlays';
import { useOffline, useOfflineLifecycle } from './hooks/useOffline';
import { OfflineStatus } from './components/offline/OfflineStatus';
import { useRealtime } from './hooks/useRealtime';
import { useDemoStatus, useMe } from './hooks/useSessionInfo';
import { useArtifactStatus } from './maps/layers/layerData';
import { legendPresence } from './maps/layers/styles';
import type { MapLayerInputs } from './maps/layers/types';
import { useLayerToggles } from './maps/useLayerToggles';
import { useAppearance } from './styles/appearance';
import { corroborateReport, disputeReport, listReports, type Incident, type JsonObject } from './services/api';
import type { EventStoreState, RealtimeEvent } from './services/eventStore';
import {
  closureFeatures,
  destinationInput,
  incidentBoundaryFC,
  indoorExit,
  indoorStarts,
  mergeById,
  pickDefaultIncident,
  reportMarkers,
  routeLines,
  startPoint,
} from './services/mapData';
import DemoAdmin from './pages/DemoAdmin';

type Raw = Record<string, unknown>;
type PickMode = 'start' | 'destination' | null;

// ------------------------------------------------------------------ small hooks
/** One shared request for the SIMULATED building so the view and the routing flow do not fetch twice. */
let sharedBuilding: Promise<IndoorGeoJson> | null = null;
const loadBuildingShared: typeof fetchIndoorBuilding = () => {
  sharedBuilding ??= fetchIndoorBuilding().catch((e: unknown) => {
    sharedBuilding = null;
    throw e;
  });
  return sharedBuilding;
};

function useIndoorGeo(enabled: boolean): IndoorGeoJson | null {
  const [geo, setGeo] = useState<IndoorGeoJson | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    loadBuildingShared().then(
      (g) => live && setGeo(g),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [enabled]);
  return enabled ? geo : null;
}

function useFetchedReports(incidentId: string | null, ready: boolean): Raw[] {
  const [state, setState] = useState<{ id: string | null; rows: Raw[] }>({ id: null, rows: [] });
  useEffect(() => {
    if (!incidentId || !ready) return;
    const ctl = new AbortController();
    listReports({ incident_id: incidentId }, ctl.signal).then(
      (r) => {
        const rows = (r as unknown as { reports?: Raw[] } | null)?.reports;
        setState({ id: incidentId, rows: Array.isArray(rows) ? rows : [] });
      },
      () => undefined,
    );
    return () => ctl.abort();
  }, [incidentId, ready]);
  return state.id === incidentId ? state.rows : [];
}

/** The Community tab reads the REST + realtime feed; the hook needs an incident id, so it lives here. */
function CommunityTabContainer({
  incidentId,
  store,
  location,
  ownAlias,
  onOpenCommunications,
}: {
  incidentId: string | null;
  store: EventStoreState | undefined;
  location: { latitude: number; longitude: number } | null;
  ownAlias: string | null;
  onOpenCommunications: () => void;
}) {
  if (!incidentId) {
    return (
      <p data-testid="community-no-incident" className="p-3 text-sm text-text-muted">
        Select an incident to see reports, messages and check-ins.
      </p>
    );
  }
  return (
    <CommunityTabWithFeed
      incidentId={incidentId}
      store={store}
      location={location}
      ownAlias={ownAlias}
      onOpenCommunications={onOpenCommunications}
    />
  );
}

function CommunityTabWithFeed({
  incidentId,
  store,
  location,
  ownAlias,
  onOpenCommunications,
}: {
  incidentId: string;
  store: EventStoreState | undefined;
  location: { latitude: number; longitude: number } | null;
  ownAlias: string | null;
  onOpenCommunications: () => void;
}) {
  const feed = useCommunityFeed(incidentId, store);
  return (
    <CommunityTab
      incidentId={incidentId}
      feed={feed}
      location={location}
      ownAlias={ownAlias}
      onOpenCommunications={onOpenCommunications}
    />
  );
}

// ------------------------------------------------------------------ shell
/** The real application: header, map stage with the iOS-style frame, routing flow, indoor mode and screens (Req 34). */
export function AppShell() {
  const auth = useAuth();
  const demo = useDemoStatus();
  const { me, loading: meLoading, error: meError } = useMe(auth.ready);
  const inc = useIncident();
  const nav = useNavigation();
  const tier = useViewportTier();
  const appearance = useAppearance();
  const artifactStatus = useArtifactStatus();
  // The setting lives in state (initialised from storage) so toggling it registers or removes the
  // global handler immediately; `onShortcutsChange` also persists it (Req 16.4, 16.5).
  const [shortcutsEnabled, setShortcutsEnabled] = useState(readShortcuts);
  const shortcuts = useKeyboardShortcuts({
    enabled: shortcutsEnabled,
    onSelectTab: nav.openTab,
    contentSelector: '[data-panel-body]',
  });

  // ---- incident selection (default: first active incident; demo mode: the flood scenario)
  const defaultIncident = useMemo(
    () => (demo.loaded && !inc.loading ? pickDefaultIncident(inc.incidents, demo.demoMode) : null),
    [demo.loaded, demo.demoMode, inc.loading, inc.incidents],
  );
  const incident: Incident | null = inc.selected ?? defaultIncident;
  const incidentId = incident?.incident_id ?? null;
  const indoor = incident?.incident_type === 'building';
  const [typeOverride, setTypeOverride] = useState<IncidentType | null>(null);
  const disasterType: IncidentType | null = typeOverride ?? incident?.incident_type ?? null;

  // ---- route inputs
  const [start, setStart] = useState<LatLng | null>(null);
  const [destination, setDestination] = useState<Destination | null>(null);
  const [pickMode, setPickMode] = useState<PickMode>(null);
  const [travelMode, setTravelMode] = useState<TravelMode>('walking');
  const [stepFree, setStepFree] = useState(false);
  const [indoorStartId, setIndoorStartId] = useState<string | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);

  const geo = useIndoorGeo(indoor);
  const starts = useMemo(() => indoorStarts(geo), [geo]);
  const exit = useMemo(() => indoorExit(geo), [geo]);
  const indoorStart = starts.find((s) => s.startId === indoorStartId) ?? starts[0] ?? null;

  const body = useMemo<JsonObject | null>(() => {
    if (!incidentId) return null;
    const common = { incident_id: incidentId, travel_mode: indoor ? 'walking' : travelMode, mobility_profile: stepFree ? 'step_free' : 'standard' };
    if (indoor) {
      if (!indoorStart || !exit) return null;
      return {
        ...common,
        origin: { latitude: indoorStart.lat, longitude: indoorStart.lng },
        destination: { latitude: exit.lat, longitude: exit.lng },
        alternatives: 1,
        origin_indoor_node_id: indoorStart.nodeId,
      };
    }
    if (!start || !destination) return null;
    return {
      ...common,
      origin: { latitude: start.lat, longitude: start.lng },
      destination: { latitude: destination.lat, longitude: destination.lng },
      alternatives: 2,
    };
  }, [incidentId, indoor, indoorStart, exit, start, destination, travelMode, stepFree]);

  // ---- realtime + routing (raw events drive both restrictions and recalculation)
  const sink = useRef<(e: RealtimeEvent) => void>(() => undefined);
  const onEvent = useCallback((e: RealtimeEvent) => sink.current(e), []);
  const rt = useRealtime(incidentId, onEvent);
  useOfflineLifecycle(incidentId ? rt : undefined);
  const off = useOffline();
  // Live restriction events (indoor view, restriction count); the map draws from the overlays snapshot.
  const restrictions = useActiveRestrictions(incidentId);
  // Hazard areas and restricted edges for the selected incident, snapshot + live events (Req 2.1-2.6, 2.11).
  const overlays = useMapOverlays(incidentId);
  const route = useActiveRoute({ incidentId, body, stale: incidentId !== null && rt.stale });
  const { onEvent: onRestrictionEvent } = restrictions;
  const { handleEvent: onOverlayEvent } = overlays;
  const { handleEvent: onRouteEvent } = route;
  useEffect(() => {
    sink.current = (e) => {
      onRestrictionEvent(e);
      onOverlayEvent(e);
      onRouteEvent(e);
    };
  }, [onRestrictionEvent, onOverlayEvent, onRouteEvent]);

  // ---- map data
  const fetchedReports = useFetchedReports(incidentId, auth.ready);
  const reportRows = useMemo(() => mergeById(fetchedReports, rt.store.reports as Record<string, Raw>, ['report_id', 'id']), [fetchedReports, rt.store.reports]);
  const markers = useMemo(() => reportMarkers(reportRows), [reportRows]);
  // Restrictions added after the snapshot have no geometry yet: draw their edges from
  // GET /api/graph/edges/{id} until the debounced refetch delivers them.
  const pendingEdgeCoords = useEdgeCoordinates(overlays.pendingEdgeIds, incidentId);
  const activeRestrictions = restrictions.restrictions;
  const pendingRestrictions = overlays.merged.pending;
  const selectedId = route.response?.routes.some((r) => r.route_id === selectedRouteId) ? selectedRouteId : null;

  const layerInputs = useMemo<MapLayerInputs>(
    () => ({
      routes: routeLines(route.response, selectedId),
      reports: markers,
      incidentBoundaries: incidentBoundaryFC(incident),
      hazards: overlays.inputs.hazards,
      restrictions: overlays.inputs.restrictions,
      // Interim lines for pending restrictions; MapLayers draws them in the restrictions style and
      // keeps the closures source for report-derived closure points.
      closures: closureFeatures(pendingRestrictions, pendingEdgeCoords),
      start: indoor ? null : startPoint(start),
      destination: indoor ? null : destinationInput(destination),
      historicalLayers: [],
    }),
    [route.response, selectedId, markers, incident, overlays.inputs, pendingRestrictions, pendingEdgeCoords, indoor, start, destination],
  );
  // The legend lists only entries whose data is on the map (Req 2.9, 2.10).
  const presence = useMemo(() => legendPresence(layerInputs, artifactStatus), [layerInputs, artifactStatus]);
  const layerToggles = useLayerToggles({ legendPresence: presence });

  // ---- inspector / map clicks
  const inspector = useFeatureInspector({ incidentId });
  const { onMapClick } = inspector;
  const handleFeatureClick = useCallback(
    (click: MapClick) => {
      const [lng, lat] = click.lngLat;
      if (pickMode === 'start') {
        setStart({ lat, lng });
        setPickMode(null);
      } else if (pickMode === 'destination') {
        setDestination({ lat, lng });
        setPickMode(null);
      } else {
        onMapClick(click);
      }
    },
    [pickMode, onMapClick],
  );

  // ---- handlers
  const resetPoints = () => {
    setStart(null);
    setDestination(null);
    setPickMode(null);
    setSelectedRouteId(null);
  };
  const selectIncident = (i: Incident) => {
    inc.select(i);
    setTypeOverride(i.incident_type);
    resetPoints();
    setIndoorStartId(null);
  };
  const [indoorReportFeature, setIndoorReportFeature] = useState<string | null>(null);

  const routePanel: RoutePanelProps = {
    response: route.response,
    loading: route.loading,
    error: route.error,
    recalc: route.recalc,
    selectedRouteId: selectedId,
    onSelectRoute: setSelectedRouteId,
    onRecalculate: route.recalculate,
  };
  const location = start ? { latitude: start.lat, longitude: start.lng } : null;

  // ---- header
  const connectivity: ConnectivityState = off.offline ? 'offline' : incidentId ? rt.status : 'idle';
  const headerProps = {
    city: 'Chicago',
    demoMode: demo.demoMode,
    connectivity: <ConnectivityStatus status={connectivity} lastSyncedAt={(incidentId ? rt.lastSyncedAt : null) ?? (off.offline ? off.lastSyncAt : null)} stale={off.offline || (incidentId !== null && rt.stale)} />,
    profile: <ProfileMenu me={me} loading={meLoading} error={meError} />,
  };

  const shelter = route.response?.policy_outcome === 'SHELTER_IN_PLACE';
  // True while a RouteChangeBanner renders (same condition as the banner), so the Route tab can show "Updated".
  const rc = route.recalc;
  const routeChanged = !!rc && (rc.stale || rc.updating || !!rc.change || !!rc.message || rc.status !== 'idle');

  const reportHazard = () => {
    setIndoorReportFeature(null);
    nav.openScreen('submit-report');
  };
  const onShortcutsChange = (enabled: boolean) => {
    setShortcutsEnabled(enabled);
    writeShortcuts(enabled);
  };

  const disasterSelector = {
    selectedType: disasterType,
    incidents: inc.incidents,
    selectedIncidentId: incidentId,
    loading: inc.loading || !demo.loaded,
    error: inc.error,
    onSelectType: setTypeOverride,
    onSelectIncident: selectIncident,
  };
  const inspectorProps = {
    inspector,
    reports: reportRows as unknown as EdgeReport[],
    routes: route.response?.routes,
  };

  const tabContent = {
    map: () => (
      <ErrorBoundary>
        <MapTab
          tier={tier}
          incident={incident}
          onReportHazard={reportHazard}
          onRequestAssistance={() => nav.openScreen('community')}
          onMarkSafe={() => nav.openScreen('safety-check-in')}
          onViewGuidance={() => nav.openScreen('official-guidance')}
          disasterSelector={disasterSelector}
          onOpenScreen={nav.openScreen}
          layerControls={layerToggles.controls}
          inspector={inspectorProps}
          indoor={indoor}
          indoorProps={{
            loadBuilding: loadBuildingShared,
            reports: reportRows,
            restrictions: Object.values(activeRestrictions) as unknown as Raw[],
            route: route.response,
            selectedStartId: indoorStart?.startId ?? null,
            onSelectStart: setIndoorStartId,
            onReport: (featureId: string) => {
              setIndoorReportFeature(featureId);
              nav.openScreen('submit-report');
            },
            onCorroborate: (id: string) => corroborateReport(id),
            onDispute: (id: string) => disputeReport(id),
          }}
        />
        {tier === 'desktop' && (
          // At Desktop the inspector lives in the Inspector_Panel, which unmounts when closed;
          // keep the announcement in a live region that stays mounted.
          <div role="status" aria-live="polite" className="sr-only" data-testid="inspect-live-desktop">
            {inspector.announcement}
          </div>
        )}
      </ErrorBoundary>
    ),
    route: () => (
      <ErrorBoundary>
        <RouteTab
          start={start}
          destination={destination}
          pickMode={pickMode}
          onPickModeChange={setPickMode}
          onStartChange={(p) => setStart(p)}
          onDestinationChange={(p) => setDestination(p)}
          showSamplePoints={disasterType === 'flood'}
          travelMode={travelMode}
          onTravelModeChange={setTravelMode}
          stepFree={stepFree}
          onStepFreeChange={setStepFree}
          indoor={indoor}
          shelter={shelter}
          routePanel={routePanel}
          onOpenRouteDetails={() => nav.openScreen('route-details')}
        />
      </ErrorBoundary>
    ),
    community: () => (
      <ErrorBoundary>
        <CommunityTabContainer
          incidentId={incidentId}
          store={rt.store}
          location={location}
          ownAlias={me?.display_name ?? null}
          onOpenCommunications={() => nav.openScreen('community')}
        />
      </ErrorBoundary>
    ),
    safety: () => (
      <ErrorBoundary>
        <SafetyTab
          onReportHazard={reportHazard}
          onRequestAssistance={() => nav.openScreen('community')}
          onMarkSafe={() => nav.openScreen('safety-check-in')}
          onViewGuidance={() => nav.openScreen('official-guidance')}
          hazard={disasterType}
        />
      </ErrorBoundary>
    ),
    more: () => (
      <ErrorBoundary>
        <MoreTab
          demoMode={demo.demoMode}
          me={me}
          meLoading={meLoading}
          meError={meError}
          appearanceSetting={appearance.setting}
          onAppearanceChange={appearance.setSetting}
          shortcutsEnabled={shortcutsEnabled}
          onShortcutsChange={onShortcutsChange}
        />
      </ErrorBoundary>
    ),
  };

  // Each tab starts with a hidden heading that shortcut navigation can focus (Req 16.10).
  const renderTab = Object.fromEntries(
    (Object.keys(tabContent) as TabId[]).map((id) => [
      id,
      () => (
        <>
          <TabHeading tab={id} />
          {tabContent[id]()}
        </>
      ),
    ]),
  ) as Record<TabId, () => ReactNode>;

  return (
    <RootLayout incidents={incident ? [incident] : []} headerProps={headerProps} banner={<OfflineStatus />}>
      {(auth.error || !auth.ready) && (
        <p
          role={auth.error ? 'alert' : 'status'}
          data-testid="session-status"
          className="border-b border-border-subtle bg-surface px-3 py-2 text-sm"
        >
          {auth.error ? `Could not start a session: ${auth.error}. Data may be unavailable.` : 'Starting a guest session…'}
        </p>
      )}
      <AppFrame
        mapProps={{
          layerInputs,
          onFeatureClick: handleFeatureClick,
          layerVisibility: layerToggles.layerVisibility,
          appearance: appearance.appearance,
        }}
        pickMode={pickMode}
        onCancelPick={() => setPickMode(null)}
        routeReady={route.response !== null}
        routeChanged={routeChanged}
        inspector={{
          open: inspector.open,
          title: inspector.selected?.title ?? 'Map feature details',
          onClose: inspector.close,
          content: (
            <ErrorBoundary>
              <FeatureInspector {...inspectorProps} />
            </ErrorBoundary>
          ),
        }}
        renderTab={renderTab}
        renderScreen={(ctx) => {
          if (ctx.demo) {
            return (
              <ScreenFrame
                title="Demo console"
                onClose={ctx.onClose}
                testId="demo-route"
                backTitle={ctx.backLabel}
                onBack={ctx.onBack}
              >
                <ErrorBoundary>
                  <DemoAdmin />
                </ErrorBoundary>
              </ScreenFrame>
            );
          }
          if (!ctx.screen) return null;
          return (
            <ErrorBoundary>
              <ScreenRouter
                screen={ctx.screen}
                onClose={ctx.onClose}
                incident={incident}
                incidents={inc.incidents}
                incidentsLoading={inc.loading}
                incidentsError={inc.error}
                disasterType={disasterType}
                store={rt.store}
                routePanel={routePanel}
                activeRestrictionCount={Object.keys(activeRestrictions).length}
                location={location}
                indoorFeatureId={indoorReportFeature}
                onClearLocation={() => {
                  setStart(null);
                  setIndoorReportFeature(null);
                }}
                onSelectType={setTypeOverride}
                onSelectIncident={selectIncident}
                onBack={ctx.onBack}
                backLabel={ctx.backLabel}
              />
            </ErrorBoundary>
          );
        }}
      />
      <div aria-live="polite" className="sr-only" data-testid="shortcut-announcer">
        {shortcuts.announcement}
      </div>
    </RootLayout>
  );
}
