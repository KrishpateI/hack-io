/**
 * Pure GeoJSON builders: turn API payloads and component props into the FeatureCollections the
 * map sources render. Every feature keeps its original properties plus `layer` and
 * `feature_kind` so the inspection popups (task 13.4) have what they need. Missing attributes
 * stay absent or "Unknown"; nothing is inferred (Req 1.6, 35.2).
 */
import {
  FLOOD_AREA_LABEL,
  HISTORICAL_LAYER_LABEL,
  SIMULATED_LABEL,
  SIMULATION_DESTINATION_LABEL,
  reportSymbol,
} from './styles';
import {
  EMPTY_FC,
  type DestinationInput,
  type FC,
  type Feat,
  type LngLat,
  type ReportMarkerInput,
  type RouteLineInput,
} from './types';

const asFeatures = (fc: FC | null | undefined): Feat[] => (fc && Array.isArray(fc.features) ? fc.features : []);
const fc = (features: Feat[]): FC => ({ type: 'FeatureCollection', features });
const isPoint = (lng: unknown, lat: unknown) =>
  typeof lng === 'number' && typeof lat === 'number' && Number.isFinite(lng) && Number.isFinite(lat);

function withProps(
  src: FC | null | undefined,
  layer: string,
  kind: string,
  extra: (f: Feat) => Record<string, unknown>,
): FC {
  return fc(
    asFeatures(src)
      .filter((f) => f && f.geometry)
      .map((f) => ({ ...f, properties: { ...(f.properties ?? {}), layer, feature_kind: kind, ...extra(f) } })),
  );
}

/** Append the historical label to CBD-derived layers (Req 8.10). */
export function applyHistorical(label: string, layer: string, historical: readonly string[] | undefined): string {
  return historical?.includes(layer) ? `${label} — ${HISTORICAL_LAYER_LABEL}` : label;
}

const DEPTH_KEY = /depth/i;
function stripDepth(props: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(props).filter(([k]) => !DEPTH_KEY.test(k)));
}

/** CTA stations: GTFS facts only; entrance safety is always Unknown (Req 6). */
export function buildStations(src: FC | null | undefined): FC {
  return withProps(src, 'cta-stations', 'cta_station', (f) => {
    const p = f.properties ?? {};
    const name = typeof p.name === 'string' && p.name ? p.name : 'Unknown';
    return {
      label: name,
      name,
      source: typeof p.source === 'string' && p.source ? p.source : 'CTA GTFS static feed',
      wheelchair_boarding: p.wheelchair_boarding ?? 'Unknown',
      entrance_safety: 'Unknown',
    };
  });
}

/** SIMULATED multi-level station: every feature carries a SIMULATED label. */
export function buildSimulatedStation(src: FC | null | undefined): FC {
  return withProps(src, 'simulated-station', 'simulated_station', (f) => {
    const name = typeof f.properties?.name === 'string' ? (f.properties.name as string) : 'station';
    return {
      simulated: true,
      label: name.toUpperCase().startsWith(SIMULATED_LABEL) ? name : `${SIMULATED_LABEL} ${name}`,
    };
  });
}

export type HazardKind = 'debris' | 'fire' | 'flood';
const HAZARD_LABELS: Record<HazardKind, string> = {
  debris: 'SIMULATED debris zone',
  fire: 'SIMULATED fire perimeter',
  flood: FLOOD_AREA_LABEL,
};

/**
 * SIMULATED hazard areas. A server label that already starts with "SIMULATED" (for example
 * "SIMULATED scenario area, not applied to routing") is kept; anything else gets the default
 * SIMULATED label. Depth-like keys are always dropped (Req 3.9, 38.3).
 */
export function buildHazard(kind: HazardKind, src: FC | null | undefined, historical?: readonly string[]): FC {
  return fc(
    withProps(src, kind, `${kind}_hazard`, (f) => {
      const server = f.properties?.label;
      const base = typeof server === 'string' && server.startsWith(SIMULATED_LABEL) ? server : HAZARD_LABELS[kind];
      return { simulated: true, label: applyHistorical(base, kind, historical) };
    }).features.map((f) => ({ ...f, properties: stripDepth(f.properties ?? {}) })),
  );
}

export function buildIncidentBoundaries(src: FC | null | undefined): FC {
  return withProps(src, 'incidents', 'incident_boundary', (f) => {
    const p = f.properties ?? {};
    const name = typeof p.name === 'string' && p.name ? p.name : 'Incident boundary';
    const sim = p.simulation_flag === true || p.simulated === true;
    return {
      simulated: sim,
      label: sim ? `${SIMULATED_LABEL} ${name}` : name,
    };
  });
}

export function buildRestrictions(src: FC | null | undefined): FC {
  return withProps(src, 'restrictions', 'restriction_edge', () => ({}));
}

export function buildRoutes(routes: readonly RouteLineInput[] | undefined): FC {
  return fc(
    (routes ?? [])
      .filter((r) => r.coordinates && r.coordinates.length >= 2)
      .map((r) => ({
        type: 'Feature' as const,
        geometry: { type: 'LineString' as const, coordinates: r.coordinates },
        properties: {
          ...(r.properties ?? {}),
          layer: 'routes',
          feature_kind: 'route',
          route_id: r.id,
          kind: r.kind,
          label: r.label ?? (r.kind === 'primary' ? 'Algorithm-generated route' : 'Alternative route'),
        },
      })),
  );
}

export interface ReportSources {
  reports: FC;
  /** Confirmed closure markers derived from reports (merged with the closures input). */
  closurePoints: Feat[];
}

/** Reports: orange for Unverified/Corroborated/Disputed with distinct shapes; red only when confirmed. */
export function buildReports(reports: readonly ReportMarkerInput[] | undefined): ReportSources {
  const out: Feat[] = [];
  const closures: Feat[] = [];
  for (const r of reports ?? []) {
    if (!isPoint(r.lng, r.lat)) continue;
    const sym = reportSymbol(r.verification_status, r.confirmed_closure === true);
    if (sym.group === 'hidden') continue;
    const feature: Feat = {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [r.lng, r.lat] },
      properties: {
        layer: sym.group,
        feature_kind: 'report',
        report_id: r.report_id,
        category: r.category,
        verification_status: r.verification_status,
        status_text: sym.statusText,
        description: r.description ?? 'Unknown',
        source_label: r.source_label ?? 'Community report',
        simulated: r.simulated === true,
        confirmations: r.confirmations ?? 0,
        disputes: r.disputes ?? 0,
        created_at: r.created_at ?? 'Unknown',
        icon: sym.icon,
        label: `${r.simulated ? `${SIMULATED_LABEL} ` : ''}${sym.statusText}: ${r.category}`,
      },
    };
    (sym.group === 'closures' ? closures : out).push(feature);
  }
  return { reports: fc(out), closurePoints: closures };
}

export function buildClosures(src: FC | null | undefined, extraPoints: Feat[]): FC {
  return fc([...withProps(src, 'closures', 'closure', () => ({})).features, ...extraPoints]);
}

/** Verified destinations need an Official source record and must not be simulated. */
export function isVerifiedDestination(d: DestinationInput): boolean {
  return d.simulated !== true && d.source_record?.is_official === true;
}

export function buildVerified(list: readonly DestinationInput[] | undefined): FC {
  return fc(
    (list ?? []).filter((d) => isVerifiedDestination(d) && isPoint(d.lng, d.lat)).map((d) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [d.lng, d.lat] },
      properties: {
        layer: 'verified',
        feature_kind: 'verified_destination',
        destination_id: d.id,
        name: d.name,
        source_record_id: d.source_record?.source_id ?? 'Unknown',
        label: d.name,
      },
    })),
  );
}

/** Start and destination markers. A simulated destination is never presented as a shelter. */
export function buildMarkers(start: LngLat | null | undefined, destination: DestinationInput | null | undefined): FC {
  const feats: Feat[] = [];
  if (start && isPoint(start[0], start[1])) {
    feats.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: start },
      properties: { layer: 'markers', feature_kind: 'start', icon: 'icon-start', label: 'Start' },
    });
  }
  if (destination && isPoint(destination.lng, destination.lat) && !isVerifiedDestination(destination)) {
    const sim = destination.simulated === true;
    feats.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [destination.lng, destination.lat] },
      properties: {
        layer: 'markers',
        feature_kind: 'destination',
        destination_id: destination.id,
        name: destination.name,
        simulated: sim,
        icon: 'icon-destination-simulated',
        label: sim ? SIMULATION_DESTINATION_LABEL : `${destination.name} (not verified as an official shelter)`,
      },
    });
  }
  return fc(feats);
}

export { EMPTY_FC };
