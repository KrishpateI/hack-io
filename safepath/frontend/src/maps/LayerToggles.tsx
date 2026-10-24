export interface LayerToggleItem {
  /** Key used by MapView; maps to one or more MapLibre layer ids. */
  id: string;
  label: string;
  visible: boolean;
}

interface Props {
  layers: LayerToggleItem[];
  onToggle: (id: string, visible: boolean) => void;
}

/** Checkbox list. Changes are reported synchronously so the map updates immediately (Req 34.3). */
export function LayerToggles({ layers, onToggle }: Props) {
  return (
    <fieldset className="rounded border border-[#2c3a5c] bg-[#111a2e]/95 p-2 text-sm text-[#e6edf7]">
      <legend className="px-1 text-xs text-[#a9b6cf]">Map layers</legend>
      {layers.map((l) => (
        <label key={l.id} className="flex cursor-pointer items-center gap-2 py-1">
          <input
            type="checkbox"
            checked={l.visible}
            onChange={(e) => onToggle(l.id, e.target.checked)}
          />
          {l.label}
        </label>
      ))}
    </fieldset>
  );
}
