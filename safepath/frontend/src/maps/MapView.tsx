import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  buildBasemapStyle,
  probePmtiles,
  registerPmtilesProtocol,
  type PmtilesKind,
} from './pmtiles';
import { CHICAGO_CENTER, EXTRACTION_LABEL, extractionAreaFeature } from './extractionArea';
import { LayerToggles, type LayerToggleItem } from './LayerToggles';

const SRC_EXTRACTION = 'extraction-area';

/** Toggle key -> MapLibre layer ids. Later tasks append entries. */
const LAYER_GROUPS: Record<string, string[]> = {
  extraction: ['extraction-area-line'],
};

const INITIAL_TOGGLES: LayerToggleItem[] = [
  { id: 'extraction', label: 'Data extraction boundary', visible: true },
];

function addExtractionLayers(map: maplibregl.Map, visible: Record<string, boolean>) {
  if (!map.getSource(SRC_EXTRACTION)) {
    map.addSource(SRC_EXTRACTION, { type: 'geojson', data: extractionAreaFeature() });
  }
  map.addLayer({
    id: 'extraction-area-line',
    type: 'line',
    source: SRC_EXTRACTION,
    layout: { visibility: visible.extraction === false ? 'none' : 'visible' },
    paint: { 'line-color': '#e879f9', 'line-width': 2, 'line-dasharray': [4, 3] },
  });
}

export function MapView({ className = '' }: { className?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const visibleRef = useRef<Record<string, boolean>>({ extraction: true });
  const [toggles, setToggles] = useState<LayerToggleItem[]>(INITIAL_TOGGLES);

  useEffect(() => {
    if (!container.current) return;
    let cancelled = false;
    const env = import.meta.env as Record<string, string | boolean | undefined>;
    const pmtilesUrl = (env.VITE_PMTILES_URL as string | undefined) || undefined;
    const kind = ((env.VITE_PMTILES_KIND as string | undefined) ?? 'raster') as PmtilesKind;

    void (async () => {
      registerPmtilesProtocol(maplibregl as never);
      const present = await probePmtiles(pmtilesUrl);
      if (cancelled || !container.current) return;
      const { style } = buildBasemapStyle({
        pmtilesUrl,
        pmtilesKind: kind,
        isDev: env.DEV === true,
        pmtilesPresent: present,
      });
      const map = new maplibregl.Map({
        container: container.current,
        style,
        center: CHICAGO_CENTER,
        zoom: 13,
        attributionControl: false, // attribution is rendered by the DOM element below
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      mapRef.current = map;
      map.on('load', () => addExtractionLayers(map, visibleRef.current));
    })();

    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  const onToggle = (id: string, visible: boolean) => {
    visibleRef.current[id] = visible;
    setToggles((prev) => prev.map((t) => (t.id === id ? { ...t, visible } : t)));
    const map = mapRef.current;
    if (!map) return;
    for (const layerId of LAYER_GROUPS[id] ?? []) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
      }
    }
  };

  const extractionVisible = toggles.find((t) => t.id === 'extraction')?.visible !== false;

  return (
    <div className={`relative h-full min-h-[320px] w-full ${className}`} data-testid="map-view">
      <div ref={container} className="absolute inset-0" />
      <div className="absolute left-2 top-2 z-10 flex flex-col gap-2">
        <LayerToggles layers={toggles} onToggle={onToggle} />
        {extractionVisible && (
          <p
            className="max-w-xs rounded border border-dashed border-[#e879f9] bg-[#111a2e]/95 px-2 py-1 text-xs text-[#e6edf7]"
            data-testid="extraction-label"
          >
            {EXTRACTION_LABEL}
          </p>
        )}
      </div>
      {/* Always-present attribution, independent of the style source (Req 3.8, 46.1). */}
      <p
        className="absolute bottom-0 right-0 z-10 bg-[#111a2e]/90 px-2 py-0.5 text-xs text-[#e6edf7]"
        data-testid="osm-attribution"
      >
        ©{' '}
        <a
          className="underline"
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noopener noreferrer"
        >
          OpenStreetMap
        </a>{' '}
        contributors
      </p>
    </div>
  );
}
