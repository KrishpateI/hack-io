import { useEffect, useMemo, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// MapLibre 6 loads its worker from a sibling URL of its own module. In a production bundle that
// sibling does not exist (the request 404s/403s and no tiles render), so ship the worker as a
// hashed asset and point MapLibre at it explicitly.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import {
  buildBasemapStyle,
  probePmtiles,
  registerPmtilesProtocol,
  type PmtilesKind,
} from './pmtiles';
import { CHICAGO_CENTER, EXTRACTION_LABEL, extractionAreaFeature } from './extractionArea';
import { LayerToggles, type LayerToggleItem } from './LayerToggles';
import { MapLayers, type MapLike } from './layers/MapLayers';
import { Legend } from './layers/Legend';
import { useArtifactLayers } from './layers/layerData';
import { DATA_LAYER_GROUPS, TOGGLES } from './layers/styles';
import type { MapLayerInputs } from './layers/types';
import { attachInspector, type InspectMapLike } from '../components/inspect/attachInspector';
import type { MapClick } from '../components/inspect/inspectModel';

if (typeof maplibregl.setWorkerUrl === 'function') maplibregl.setWorkerUrl(maplibreWorkerUrl);

const SRC_EXTRACTION = 'extraction-area';

/** Toggle key -> MapLibre layer ids. Later tasks append entries. */
const LAYER_GROUPS: Record<string, string[]> = {
  extraction: ['extraction-area-line'],
  ...DATA_LAYER_GROUPS,
};

const INITIAL_TOGGLES: LayerToggleItem[] = [
  { id: 'extraction', label: 'Data extraction boundary', visible: true },
  ...TOGGLES.map((t) => ({ id: t.id, label: t.label, visible: t.defaultVisible })),
];
const INITIAL_VISIBLE: Record<string, boolean> = Object.fromEntries(INITIAL_TOGGLES.map((t) => [t.id, t.visible]));

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

interface MapViewProps {
  className?: string;
  /** Route lines, reports, hazards, markers etc. to draw (accepted as plain data). */
  layerInputs?: MapLayerInputs;
  /** Called with the features under the pointer on every map click (feature inspection, Req 35). */
  onFeatureClick?: (click: MapClick) => void;
}

export function MapView({ className = '', layerInputs, onFeatureClick }: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const visibleRef = useRef<Record<string, boolean>>({ ...INITIAL_VISIBLE });
  const layersRef = useRef<MapLayers | null>(null);
  const inputsRef = useRef<MapLayerInputs | undefined>(layerInputs);
  const clickRef = useRef(onFeatureClick);
  const [manager, setManager] = useState<MapLayers | null>(null);
  const [toggles, setToggles] = useState<LayerToggleItem[]>(INITIAL_TOGGLES);
  const [mapError, setMapError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    let cancelled = false;
    let detach: (() => void) | null = null;
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
      let map: maplibregl.Map;
      try {
        map = new maplibregl.Map({
          container: container.current,
          style,
          center: CHICAGO_CENTER,
          zoom: 13,
          attributionControl: false, // attribution is rendered by the DOM element below
        });
      } catch (err) {
        // No WebGL2 (old browser, blocked GPU): the rest of the app keeps working without the map.
        setMapError(err instanceof Error ? err.message : 'The map could not start.');
        return;
      }
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      mapRef.current = map;
      detach = attachInspector(map as unknown as InspectMapLike, (click) => clickRef.current?.(click));
      map.on('load', () => {
        addExtractionLayers(map, visibleRef.current);
        const glyphs = (env.VITE_GLYPHS_URL as string | undefined) || undefined;
        const m = new MapLayers(map as unknown as MapLike, visibleRef.current, glyphs);
        m.init();
        if (inputsRef.current) m.setInputs(inputsRef.current);
        layersRef.current = m;
        setManager(m);
      });
    })();

    return () => {
      cancelled = true;
      detach?.();
      mapRef.current?.remove();
      mapRef.current = null;
      layersRef.current = null;
      setManager(null);
    };
  }, []);

  useEffect(() => {
    clickRef.current = onFeatureClick;
  }, [onFeatureClick]);

  useEffect(() => {
    inputsRef.current = layerInputs;
    if (layerInputs) layersRef.current?.setInputs(layerInputs);
  }, [layerInputs, manager]);

  const toggleState = useMemo(() => Object.fromEntries(toggles.map((t) => [t.id, t.visible])), [toggles]);
  useArtifactLayers(manager, toggleState);

  const onToggle = (id: string, visible: boolean) => {
    visibleRef.current[id] = visible;
    setToggles((prev) => prev.map((t) => (t.id === id ? { ...t, visible } : t)));
    layersRef.current?.setVisible(id, visible);
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
      {/* Inline style on purpose: maplibre-gl.css (unlayered) sets .maplibregl-map { position: relative },
          which beats Tailwind's layered .absolute and collapses the map to height 0. */}
      <div ref={container} style={{ position: 'absolute', inset: 0 }} data-testid="map-canvas-container" />
      {mapError && (
        <p
          role="alert"
          data-testid="map-error"
          className="absolute inset-x-2 bottom-8 z-10 rounded border-2 border-unverified bg-[#111a2e] p-2 text-sm text-[#e6edf7]"
        >
          The map is unavailable on this device ({mapError}). Routes, reports and guidance still work in the side panels.
        </p>
      )}
      <div className="absolute left-2 top-2 z-10 flex max-h-[calc(100%-2.5rem)] max-w-[calc(100%-4rem)] flex-col gap-2 overflow-y-auto">
        <details className="rounded border border-[#2c3a5c] bg-[#111a2e]/95 text-sm text-[#e6edf7]" data-testid="map-panel">
          <summary className="min-h-11 cursor-pointer px-2 py-2.5 font-semibold">Map layers and legend</summary>
          <div className="space-y-2 p-1">
            <LayerToggles layers={toggles} onToggle={onToggle} />
            <Legend
              visible={toggleState}
              historicalLayers={layerInputs?.historicalLayers}
            />
          </div>
        </details>
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
