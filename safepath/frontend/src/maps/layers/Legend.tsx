import { formatStamp } from '../../components/offline/OfflineStatus';
import { useOffline } from '../../hooks/useOffline';
import { HISTORICAL_LAYER_LABEL, visibleLegend, type LegendEntry, type LegendPresence } from './styles';

const POLYS: Record<string, string> = {
  triangle: '8,1 15,14 1,14',
  diamond: '8,1 15,8 8,15 1,8',
  square: '2,2 14,2 14,14 2,14',
  hexagon: '8,1 14,4.5 14,11.5 8,15 2,11.5 2,4.5',
  star: '8,1 10,6 15,6 11,9.5 12.5,15 8,11.5 3.5,15 5,9.5 1,6 6,6',
  cross: '2,4 4,2 8,6 12,2 14,4 10,8 14,12 12,14 8,10 4,14 2,12 6,8',
};

function Swatch({ entry }: { entry: LegendEntry }) {
  const { shape, color, dashed, hollow } = entry;
  return (
    <svg width="22" height="16" viewBox="0 0 16 16" aria-hidden="true" className="shrink-0">
      {shape === 'line' ? (
        <line x1="0" y1="8" x2="16" y2="8" stroke={color} strokeWidth="3" strokeDasharray={dashed ? '3 2' : undefined} />
      ) : shape === 'fill' ? (
        <rect x="1" y="2" width="14" height="12" fill={color} fillOpacity="0.55" stroke={color} strokeWidth="1.5" strokeDasharray={dashed ? '2 2' : undefined} />
      ) : shape === 'circle' ? (
        <circle cx="8" cy="8" r="6" fill={hollow ? 'none' : color} stroke={color} strokeWidth="2" />
      ) : (
        <polygon points={POLYS[shape]} fill={hollow ? 'none' : color} stroke={color} strokeWidth="2" strokeLinejoin="round" />
      )}
    </svg>
  );
}

interface Props {
  /** Toggle id -> visible. Entries of hidden layers are omitted. */
  visible: Record<string, boolean>;
  /** Layers derived from CBD data show the historical label (Req 8.10). */
  historicalLayers?: string[];
  /** Entry id -> data present (from `legendPresence`). When given, entries without data are omitted. */
  presence?: LegendPresence;
}

/** Text legend: every symbol has a text description, so meaning never relies on color alone. */
export function Legend({ visible, historicalLayers, presence }: Props) {
  const entries = visibleLegend(visible, presence);
  const off = useOffline();
  const showCached = off.layersCached || off.offline;
  return (
    <section
      aria-label="Map legend"
      data-testid="map-legend"
      className="max-h-64 overflow-y-auto rounded-[var(--radius-card)] border border-border-subtle bg-surface p-3 text-xs text-text-primary"
    >
      <h2 className="mb-1 text-xs font-semibold text-text-muted">Legend</h2>
      <ul className="space-y-1">
        {entries.map((e) => (
          <li key={e.id} className="flex items-center gap-2" data-testid={`legend-${e.id}`}>
            <Swatch entry={e} />
            <span>{e.label}</span>
          </li>
        ))}
      </ul>
      {showCached && (
        <p className="mt-1 text-unverified" data-testid="legend-cached">
          Cached map data, last synced {formatStamp(off.cachedLayersAt ?? off.lastSyncAt)}
        </p>
      )}
      {historicalLayers && historicalLayers.length > 0 && (
        <p className="mt-1 text-unverified" data-testid="legend-historical">
          {HISTORICAL_LAYER_LABEL}
        </p>
      )}
    </section>
  );
}
