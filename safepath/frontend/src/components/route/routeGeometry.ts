import { palette } from '../../styles/tokens';
import type { RouteResponse } from '../../services/api';

type RouteT = RouteResponse['routes'][number];

export interface MapRouteStyle {
  color: string;
  width: number;
  /** MapLibre line-dasharray; null for a solid line. Alternatives are dashed so style is not color-only. */
  dasharray: number[] | null;
  opacity: number;
}

export interface MapRoute {
  /** Route id from the API (stable across renders). */
  id: string;
  routeType: string;
  primary: boolean;
  selected: boolean;
  /** GeoJSON Feature<LineString>; properties carry id/primary/selected for data-driven styling. */
  feature: {
    type: 'Feature';
    id: string;
    geometry: { type: 'LineString'; coordinates: number[][] };
    properties: { route_id: string; route_type: string; primary: boolean; selected: boolean; label: string };
  };
  style: MapRouteStyle;
}

const PRIMARY_STYLE: MapRouteStyle = { color: palette.route, width: 6, dasharray: null, opacity: 1 };
const ALT_STYLE: MapRouteStyle = { color: palette.routeAlt, width: 4, dasharray: [2, 2], opacity: 0.85 };

/**
 * Map a route response to drawable routes. Only ROUTE_OPTIONS_AVAILABLE responses produce routes;
 * every other outcome (shelter in place, follow instructions, no route, insufficient data) yields
 * an empty list so nothing is ever drawn or fabricated. Routes without usable geometry are skipped.
 * The first route is the primary; the rest are dashed alternatives.
 */
export function toMapRoutes(response: RouteResponse | null | undefined, selectedId?: string | null): MapRoute[] {
  if (!response || response.policy_outcome !== 'ROUTE_OPTIONS_AVAILABLE') return [];
  const usable = response.routes.filter((r: RouteT) => (r.geometry?.coordinates?.length ?? 0) >= 2);
  const sel = selectedId ?? usable[0]?.route_id ?? null;
  return usable.map((r, i): MapRoute => {
    const primary = i === 0;
    const selected = r.route_id === sel;
    const base = primary ? PRIMARY_STYLE : ALT_STYLE;
    return {
      id: r.route_id,
      routeType: r.route_type,
      primary,
      selected,
      feature: {
        type: 'Feature',
        id: r.route_id,
        geometry: { type: 'LineString', coordinates: r.geometry!.coordinates },
        properties: { route_id: r.route_id, route_type: r.route_type, primary, selected, label: r.label },
      },
      style: selected ? { ...base, width: base.width + 2, opacity: 1 } : base,
    };
  });
}

/** FeatureCollection form for a single GeoJSON source. */
export function toFeatureCollection(routes: MapRoute[]) {
  return { type: 'FeatureCollection' as const, features: routes.map((r) => r.feature) };
}
