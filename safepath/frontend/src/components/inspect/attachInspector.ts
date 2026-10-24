import { INTERACTIVE_LAYER_IDS, type MapClick, type MapHit } from './inspectModel';

/** The small slice of the MapLibre map API the inspector needs (lets tests use a fake map). */
export interface InspectMapLike {
  on(type: 'click', listener: (e: InspectClickEvent) => void): unknown;
  off(type: 'click', listener: (e: InspectClickEvent) => void): unknown;
  queryRenderedFeatures(
    geometry: unknown,
    options?: { layers?: string[] },
  ): { layer?: { id?: string }; properties?: Record<string, unknown> | null }[];
  getLayer(id: string): unknown;
}

export interface InspectClickEvent {
  point: { x: number; y: number };
  lngLat: { lng: number; lat: number };
}

/** MapLibre stringifies array/object feature properties; restore them. */
export function parseProperties(props: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props ?? {})) {
    if (typeof v === 'string' && (v.startsWith('[') || v.startsWith('{'))) {
      try {
        out[k] = JSON.parse(v);
        continue;
      } catch {
        /* keep the raw string */
      }
    }
    out[k] = v;
  }
  return out;
}

/** Convert a click event into a MapClick using only layers that exist on the map. */
export function readClick(map: InspectMapLike, e: InspectClickEvent): MapClick {
  const layers = INTERACTIVE_LAYER_IDS.filter((id) => map.getLayer(id));
  const features = layers.length ? map.queryRenderedFeatures(e.point, { layers }) : [];
  const hits: MapHit[] = [];
  for (const f of features) {
    const layerId = f.layer?.id;
    if (layerId) hits.push({ layerId, properties: parseProperties(f.properties) });
  }
  return { lngLat: [e.lngLat.lng, e.lngLat.lat], hits };
}

/**
 * Listen for clicks on the map and report what is under the pointer. Returns a detach function.
 * The callback runs synchronously inside the click handler (Req 35: within 1 s).
 */
export function attachInspector(map: InspectMapLike, onSelect: (click: MapClick) => void): () => void {
  const handler = (e: InspectClickEvent) => onSelect(readClick(map, e));
  map.on('click', handler);
  return () => {
    map.off('click', handler);
  };
}
