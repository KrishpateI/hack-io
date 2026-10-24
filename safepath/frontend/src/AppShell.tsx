import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { IndoorBuildingView } from './components/indoor/IndoorBuildingView';
import { fetchIndoorBuilding } from './components/indoor/indoorService';
import type { IndoorGeoJson } from './components/indoor/indoorModel';
import { FeatureInspector, useFeatureInspector } from './components/inspect';
import type { MapClick } from './components/inspect/inspectModel';
import type { EdgeReport } from './components/inspect/edgeService';
import { RootLayout } from './components/RootLayout';
import { RoutePanel, type RoutePanelProps } from './components/route/RoutePanel';
import { BottomControls } from './components/shell/BottomControls';
import { ConnectivityStatus, type ConnectivityState } from './components/shell/ConnectivityStatus';
import { ErrorBoundary } from './components/shell/ErrorBoundary';
import { ProfileMenu } from './components/shell/ProfileMenu';
import { RightSidebar } from './components/shell/RightSidebar';
import { ScreenRouter } from './components/shell/ScreenRouter';
import { SCREEN_TITLES, screenHash, type ScreenId } from './components/shell/screens';
import { useScreenNav } from './components/shell/useScreenNav';
import type { IncidentType } from './components/sidebar/disasters';
import { LeftSidebar } from './components/sidebar/LeftSidebar';
import type { Destination, LatLng } from './components/sidebar/StartDestinationPicker';
import { useActiveRestrictions, useEdgeCoordinates } from './hooks/useActiveRestrictions';
import { useActiveRoute } from './hooks/useActiveRoute';
import { useAuth } from './hooks/useAuth';
import { useIncident } from './hooks/useIncident';
import { useOffline, useOfflineLifecycle } from './hooks/useOffline';
import { OfflineStatus } from './components/offline/OfflineStatus';
import { useRealtime } from './hooks/useRealtime';
import { useDemoStatus, useMe } from './hooks/useSessionInfo';
import { MapView } from './maps/MapView';
import type { MapLayerInputs } from './maps/layers/types';
import { corroborateReport, disputeReport, listReports, type Incident, type JsonObject } from './services/api';
import type { RealtimeEvent } from './services/eventStore';
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
const subscribeHash = (fn: () => void) => {
  window.addEventListener('hashchange', fn);
  return () => window.removeEventListener('hashchange', fn);
};
const getHash = () => window.location.hash;

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

const SCREEN_LINKS: readonly ScreenId[] = ['disaster-selection', 'route-details', 'incident-overview', 'community', 'data-sources'];

const MODES = [
  { value: 'walking', label: 'Walking' },
  { value: 'wheelchair', label: 'Wheelchair' },
  { value: 'driving', label: 'Driving' },
] as const;

// ------------------------------------------------------------------ shell
/** The real application: header, sidebars, map, routing flow, indoor mode and screens (Req 34). */
export function AppShell() {
  const auth = useAuth();
  const demo = useDemoStatus();
  const { me, loading: meLoading, error: meError } = useMe(auth.ready);
  const inc = useIncident();
  const nav = useScreenNav();
  const hash = useSyncExternalStore(subscribeHash, getHash, () => '');
  const demoRoute = hash === '#/demo';

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
  const [travelMode, setTravelMode] = useState<(typeof MODES)[number]['value']>('walking');
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
  const restrictions = useActiveRestrictions(incidentId);
  const route = useActiveRoute({ incidentId, body, stale: incidentId !== null && rt.stale });
  const { onEvent: onRestrictionEvent } = restrictions;
  const { handleEvent: onRouteEvent } = route;
  useEffect(() => {
    sink.current = (e) => {
      onRestrictionEvent(e);
      onRouteEvent(e);
    };
  }, [onRestrictionEvent, onRouteEvent]);

  // ---- map data
  const fetchedReports = useFetchedReports(incidentId, auth.ready);
  const reportRows = useMemo(() => mergeById(fetchedReports, rt.store.reports as Record<string, Raw>, ['report_id', 'id']), [fetchedReports, rt.store.reports]);
  const markers = useMemo(() => reportMarkers(reportRows), [reportRows]);
  const edgeIds = useMemo(() => Object.values(restrictions.restrictions).flatMap((r) => r.edge_ids), [restrictions.restrictions]);
  const edgeCoords = useEdgeCoordinates(edgeIds, incidentId);
  const activeRestrictions = restrictions.restrictions;
  const selectedId = route.response?.routes.some((r) => r.route_id === selectedRouteId) ? selectedRouteId : null;

  const layerInputs = useMemo<MapLayerInputs>(
    () => ({
      routes: routeLines(route.response, selectedId),
      reports: markers,
      incidentBoundaries: incidentBoundaryFC(incident),
      closures: closureFeatures(activeRestrictions, edgeCoords),
      start: indoor ? null : startPoint(start),
      destination: indoor ? null : destinationInput(destination),
      historicalLayers: [],
    }),
    [route.response, selectedId, markers, incident, activeRestrictions, edgeCoords, indoor, start, destination],
  );

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
  const recalculating = route.loading || route.recalc.updating;
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
  const screenOpen = nav.screen !== null || demoRoute;

  return (
    <RootLayout incidents={incident ? [incident] : []} headerProps={headerProps} banner={<OfflineStatus />}>
      {demoRoute && (
        <div className="p-3" data-testid="demo-route">
          <a href="#/" className="mb-3 inline-flex min-h-11 items-center rounded border border-route px-3 text-sm font-semibold text-route">
            Back to the map
          </a>
          <ErrorBoundary>
            <DemoAdmin />
          </ErrorBoundary>
        </div>
      )}
      {nav.screen && !demoRoute && (
        <ErrorBoundary>
          <ScreenRouter
            screen={nav.screen}
            onClose={nav.close}
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
          />
        </ErrorBoundary>
      )}
      <div hidden={screenOpen} data-testid="dashboard" className="pb-24">
        <ErrorBoundary>
          {(auth.error || !auth.ready) && (
            <p
              role={auth.error ? 'alert' : 'status'}
              data-testid="session-status"
              className="border-b border-border-subtle bg-surface px-3 py-2 text-sm"
            >
              {auth.error ? `Could not start a session: ${auth.error}. Data may be unavailable.` : 'Starting a guest session…'}
            </p>
          )}
          <div className="grid min-w-0 grid-cols-1 lg:grid-cols-[20rem_minmax(0,1fr)_24rem]">
            <div className="order-3 min-w-0 border-t border-border-subtle lg:order-1 lg:border-r lg:border-t-0 [&>aside]:w-full!">
              <LeftSidebar
                incident={incident}
                incidents={inc.incidents}
                incidentsLoading={inc.loading || !demo.loaded}
                incidentsError={inc.error}
                disasterType={disasterType}
                start={start}
                destination={destination}
                pickMode={pickMode}
                routeResponse={route.response}
                onSelectType={setTypeOverride}
                onSelectIncident={selectIncident}
                onStartChange={(p) => setStart(p)}
                onDestinationChange={(p) => setDestination(p)}
                onPickModeChange={setPickMode}
              />
            </div>

            <section aria-label="Map and route" className="order-1 min-w-0 lg:order-2">
              <nav aria-label="Screens" className="flex flex-wrap gap-2 border-b border-border-subtle bg-surface p-2">
                {SCREEN_LINKS.map((id) => (
                  <a
                    key={id}
                    href={screenHash(id)}
                    className="inline-flex min-h-11 items-center rounded border border-border-subtle px-3 text-sm text-text-primary underline"
                  >
                    {SCREEN_TITLES[id]}
                  </a>
                ))}
                {demo.demoMode && (
                  <a
                    href="#/demo"
                    className="inline-flex min-h-11 items-center rounded border border-unverified px-3 text-sm text-unverified"
                  >
                    Demo console
                  </a>
                )}
              </nav>

              {!indoor && (
                <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border-subtle px-3 py-2 text-sm">
                  <legend className="sr-only">Route options</legend>
                  <label className="flex items-center gap-2">
                    Travel mode
                    <select
                      value={travelMode}
                      onChange={(e) => setTravelMode(e.target.value as typeof travelMode)}
                      className="min-h-11 rounded border border-border-subtle bg-surface px-2 text-text-primary"
                    >
                      {MODES.map((m) => (
                        <option key={m.value} value={m.value}>{m.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="flex min-h-11 items-center gap-2">
                    <input type="checkbox" checked={stepFree} onChange={(e) => setStepFree(e.target.checked)} />
                    Step-free route
                  </label>
                </fieldset>
              )}

              {pickMode && (
                <p role="status" data-testid="pick-hint" className="border-b border-route bg-surface-raised px-3 py-2 text-sm text-route">
                  Click the map to set the {pickMode}. Press the pick button again to cancel.
                </p>
              )}
              {shelter && (
                <p role="status" data-testid="shelter-note" className="border-b border-closure px-3 py-2 text-sm font-semibold">
                  Shelter in place: no route is shown. Follow the guidance below.
                </p>
              )}

              {indoor ? (
                <div className="p-2" data-testid="indoor-area">
                  <IndoorBuildingView
                    loadBuilding={loadBuildingShared}
                    reports={reportRows}
                    restrictions={Object.values(activeRestrictions) as unknown as Raw[]}
                    route={route.response}
                    selectedStartId={indoorStart?.startId ?? null}
                    onSelectStart={setIndoorStartId}
                    onReport={(featureId) => {
                      setIndoorReportFeature(featureId);
                      nav.open('submit-report');
                    }}
                    onCorroborate={(id) => corroborateReport(id)}
                    onDispute={(id) => disputeReport(id)}
                  />
                </div>
              ) : (
                <div className="relative h-[60vh] min-h-[320px] w-full lg:h-[68vh] lg:min-h-[480px]" data-testid="map-area">
                  <MapView layerInputs={layerInputs} onFeatureClick={handleFeatureClick} />
                </div>
              )}

              <FeatureInspector
                inspector={inspector}
                reports={reportRows as unknown as EdgeReport[]}
                routes={route.response?.routes}
              />
              <RoutePanel {...routePanel} />
            </section>

            <div className="order-4 min-w-0 border-t border-border-subtle lg:order-3 lg:border-l lg:border-t-0 [&>aside]:w-full!">
              {incidentId ? (
                <RightSidebar incidentId={incidentId} store={rt.store} location={location} ownAlias={me?.display_name ?? null} />
              ) : (
                <aside aria-label="Community and official updates" className="bg-surface p-3 text-sm text-text-muted">
                  Select an incident to see reports, messages and check-ins.
                </aside>
              )}
            </div>

            <div className="order-2 min-w-0 lg:order-5 lg:col-span-3">
              <BottomControls
                onReportHazard={() => {
                  setIndoorReportFeature(null);
                  nav.open('submit-report');
                }}
                onRequestAssistance={() => nav.open('community')}
                onMarkSafe={() => nav.open('safety-check-in')}
                onViewGuidance={() => nav.open('official-guidance')}
                onRecalculate={route.recalculate}
                recalculating={recalculating}
              />
            </div>
          </div>
        </ErrorBoundary>
      </div>
    </RootLayout>
  );
}
