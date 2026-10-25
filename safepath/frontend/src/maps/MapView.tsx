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
import { applyBasemapAppearance, type BasemapAppearanceMap, type BasemapKind } from './basemapAppearance';
import { CHICAGO_CENTER, EXTRACTION_LABEL, extractionAreaFeature } from './extractionArea';
import { MapLayers, type MapLike } from './layers/MapLayers';
import { useArtifactLayers } from './layers/layerData';
import { EXTRACTION_COLOR, TOGGLES } from './layers/styles';
import { DEFAULT_LAYER_VISIBILITY, LAYER_GROUPS } from './useLayerToggles';
import type { MapLayerInputs } from './layers/types';
import { attachInspector, type InspectMapLike } from '../components/inspect/attachInspector';
import type { MapClick } from '../components/inspect/inspectModel';
import { useReducedMotion } from '../components/ios/hooks/useReducedMotion';
import type { Appearance } from '../styles/tokens';

if (typeof maplibregl.setWorkerUrl === 'function') maplibregl.setWorkerUrl(maplibreWorkerUrl);

const SRC_EXTRACTION = 'extraction-area';
const PADDING_ANIMATION_MS = 250;

function addExtractionLayers(map: maplibregl.Map, visible: Record<string, boolean>) {
  if (!map.getSource(SRC_EXTRACTION)) {
    map.addSource(SRC_EXTRACTION, { type: 'geojson', data: extractionAreaFeature() });
  }
  map.addLayer({
    id: 'extraction-area-line',
    type: 'line',
    source: SRC_EXTRACTION,
    layout: { visibility: visible.extraction === false ? 'none' : 'visible' },
    paint: { 'line-color': EXTRACTION_COLOR, 'line-width': 2, 'line-dasharray': [4, 3] },
  });
}

interface MapViewProps {
  className?: string;
  /** Route lines, reports, hazards, markers etc. to draw (accepted as plain data). */
  layerInputs?: MapLayerInputs;
  /** Called with the features under the pointer on every map click (feature inspection, Req 35). */
  onFeatureClick?: (click: MapClick) => void;
  /**
   * Area (px) covered by panels. The map re-centers on the visible area through `setPadding`,
   * so the initial center and fitBounds ignore what the Side_Panel, Inspector_Panel, or sheet hide.
   */
  viewPadding?: { top?: number; right?: number; bottom?: number; left?: number };
  /**
   * Controlled layer visibility: toggle id -> visible (see `useLayerToggles`). Missing ids use their
   * default. Changes reach the map layers, the extraction boundary, and the artifact fetches at once.
   */
  layerVisibility?: Record<string, boolean>;
  /** Resolved Appearance for the basemap and adaptive labels. Defaults to dark. */
  appearance?: Appearance;
}

export function MapView({
  className = '',
  layerInputs,
  onFeatureClick,
  viewPadding,
  layerVisibility,
  appearance = 'dark',
}: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const layersRef = useRef<MapLayers | null>(null);
  const inputsRef = useRef<MapLayerInputs | undefined>(layerInputs);
  const clickRef = useRef(onFeatureClick);
  const basemapKindRef = useRef<BasemapKind>('none');
  const paddedOnce = useRef(false);
  const reducedMotion = useReducedMotion();
  const [manager, setManager] = useState<MapLayers | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);

  const visibility = useMemo(
    () => ({ ...DEFAULT_LAYER_VISIBILITY, ...layerVisibility }),
    [layerVisibility],
  );
  // Refs let the one-time map setup read the latest values without re-creating the map.
  const visibleRef = useRef(visibility);
  const appearanceRef = useRef(appearance);
  useEffect(() => {
    visibleRef.current = visibility;
    appearanceRef.current = appearance;
  }, [visibility, appearance]);

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
      const { style, source } = buildBasemapStyle(
        {
          pmtilesUrl,
          pmtilesKind: kind,
          isDev: env.DEV === true,
          pmtilesPresent: present,
        },
        appearanceRef.current,
      );
      basemapKindRef.current = source === 'none' ? 'none' : source === 'pmtiles' ? kind : 'raster';
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
        m.setAppearance(appearanceRef.current);
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
      paddedOnce.current = false;
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

  // Appearance: paint-only updates, so the style source, URL, and attribution never change.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    applyBasemapAppearance(map as unknown as BasemapAppearanceMap, appearance, basemapKindRef.current);
    layersRef.current?.setAppearance(appearance);
  }, [appearance, manager]);

  const padTop = viewPadding?.top ?? 0;
  const padRight = viewPadding?.right ?? 0;
  const padBottom = viewPadding?.bottom ?? 0;
  const padLeft = viewPadding?.left ?? 0;
  useEffect(() => {
    const map = mapRef.current as {
      setPadding?: (p: Record<string, number>) => void;
      easeTo?: (o: Record<string, unknown>) => void;
    } | null;
    if (!map) return;
    const padding = { top: padTop, right: padRight, bottom: padBottom, left: padLeft };
    // The first application and reduced-motion users get an instant change; later changes ease.
    if (!reducedMotion && paddedOnce.current && map.easeTo) {
      map.easeTo({ padding, duration: PADDING_ANIMATION_MS });
    } else {
      map.setPadding?.(padding);
    }
    paddedOnce.current = true;
  }, [padTop, padRight, padBottom, padLeft, manager, reducedMotion]);

  // Visibility is controlled by the parent: apply every change to the map right away (Req 34.3).
  useEffect(() => {
    const map = mapRef.current;
    if (map) {
      for (const layerId of LAYER_GROUPS.extraction) {
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(layerId, 'visibility', visibility.extraction === false ? 'none' : 'visible');
        }
      }
    }
    if (!manager) return;
    for (const t of TOGGLES) {
      const next = visibility[t.id] !== false;
      if (manager.isVisible(t.id) !== next) manager.setVisible(t.id, next);
    }
  }, [visibility, manager]);

  useArtifactLayers(manager, visibility);

  const extractionVisible = visibility.extraction !== false;

  return (
    <div className={`relative h-full min-h-[320px] w-full ${className}`} data-testid="map-view">
      {/* Inline style on purpose: maplibre-gl.css (unlayered) sets .maplibregl-map { position: relative },
          which beats Tailwind's layered .absolute and collapses the map to height 0. */}
      <div ref={container} style={{ position: 'absolute', inset: 0 }} data-testid="map-canvas-container" />
      {mapError && (
        <p
          role="alert"
          data-testid="map-error"
          className="absolute inset-x-2 bottom-8 z-10 rounded border-2 border-unverified bg-surface p-2 text-sm text-text-primary"
        >
          The map is unavailable on this device ({mapError}). Routes, reports and guidance still work in the side panels.
        </p>
      )}
      {extractionVisible && (
        <p
          className="absolute z-10 max-w-xs rounded border border-dashed border-control-border bg-glass px-2 py-1 text-xs text-text-primary"
          style={{ left: `calc(0.5rem + ${padLeft}px)`, top: `calc(0.5rem + ${padTop}px)` }}
          data-testid="extraction-label"
        >
          {EXTRACTION_LABEL}
        </p>
      )}
      {/* Always-present attribution, independent of the style source (Req 3.8, 46.1). */}
      <p
        className="absolute right-0 z-10 bg-surface/90 px-2 py-0.5 text-xs text-text-primary"
        style={{ bottom: 'var(--map-attribution-bottom, 0px)' }}
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
