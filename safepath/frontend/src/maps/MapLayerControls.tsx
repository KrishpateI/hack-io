import { GroupedList } from '../components/ios/GroupedList';
import { ToggleSwitch } from '../components/ios/ToggleSwitch';
import { Legend } from './layers/Legend';
import type { LegendPresence } from './layers/styles';

export interface LayerToggleItem {
  /** Key used by MapView; maps to one or more MapLibre layer ids. */
  id: string;
  label: string;
  visible: boolean;
}

export interface MapLayerControlsProps {
  layers: LayerToggleItem[];
  /** Reports a change synchronously so the map updates immediately (Req 34.3). */
  onToggle: (id: string, visible: boolean) => void;
  /** Layers derived from CBD data show the historical label in the legend (Req 8.10). */
  historicalLayers?: string[];
  /** Legend entry id -> data present; entries without data are omitted when given (Req 2.9). */
  legendPresence?: LegendPresence;
}

/**
 * Map layer controls for the Map tab: a Grouped_List of Toggle_Switches (each named by the
 * layer name, with a text On/Off state) followed by the existing Legend.
 */
export function MapLayerControls({ layers, onToggle, historicalLayers, legendPresence }: MapLayerControlsProps) {
  const visible = Object.fromEntries(layers.map((l) => [l.id, l.visible]));
  return (
    <div data-testid="map-layer-controls" className="flex flex-col gap-4">
      <GroupedList header="Map layers" aria-label="Map layers">
        {layers.map((l) => (
          <ToggleSwitch key={l.id} label={l.label} checked={l.visible} onChange={(v) => onToggle(l.id, v)} />
        ))}
      </GroupedList>
      <Legend visible={visible} historicalLayers={historicalLayers} presence={legendPresence} />
    </div>
  );
}
