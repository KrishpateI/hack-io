import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { MapLayerControls, type LayerToggleItem } from './MapLayerControls';
import { DATA_LAYER_GROUPS, TOGGLES, type LegendPresence } from './layers/styles';

/** Toggle key -> MapLibre layer ids. Later tasks append entries. */
export const LAYER_GROUPS: Record<string, string[]> = {
  extraction: ['extraction-area-line'],
  ...DATA_LAYER_GROUPS,
};

export const INITIAL_TOGGLES: LayerToggleItem[] = [
  { id: 'extraction', label: 'Data extraction boundary', visible: true },
  ...TOGGLES.map((t) => ({ id: t.id, label: t.label, visible: t.defaultVisible })),
];

/** Visibility by toggle id when nothing has been toggled yet. */
export const DEFAULT_LAYER_VISIBILITY: Record<string, boolean> = Object.fromEntries(
  INITIAL_TOGGLES.map((t) => [t.id, t.visible]),
);

export interface UseLayerToggles {
  /** Toggle id -> visible. Pass to `MapView`. */
  layerVisibility: Record<string, boolean>;
  toggles: LayerToggleItem[];
  setLayerVisible: (id: string, visible: boolean) => void;
  /** `MapLayerControls` bound to this state. Pass to `MapTab`'s `layerControls` slot. */
  controls: ReactNode;
}

/** Owns the layer visibility state that `MapView` used to keep internally. */
export function useLayerToggles(
  options: { historicalLayers?: string[]; legendPresence?: LegendPresence } = {},
): UseLayerToggles {
  const { historicalLayers, legendPresence } = options;
  const [layerVisibility, setVisibility] = useState<Record<string, boolean>>(DEFAULT_LAYER_VISIBILITY);

  const setLayerVisible = useCallback((id: string, visible: boolean) => {
    setVisibility((prev) => (prev[id] === visible ? prev : { ...prev, [id]: visible }));
  }, []);

  const toggles = useMemo(
    () => INITIAL_TOGGLES.map((t) => ({ ...t, visible: layerVisibility[t.id] !== false })),
    [layerVisibility],
  );

  const controls = useMemo(
    () => (
      <MapLayerControls
        layers={toggles}
        onToggle={setLayerVisible}
        historicalLayers={historicalLayers}
        legendPresence={legendPresence}
      />
    ),
    [toggles, setLayerVisible, historicalLayers, legendPresence],
  );

  return { layerVisibility, toggles, setLayerVisible, controls };
}
