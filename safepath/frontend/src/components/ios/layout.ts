/**
 * Pure layout helpers for the iOS-style frames (viewport tiers, stage geometry,
 * Bottom_Sheet detents). No DOM access; every function is deterministic.
 */

export type ViewportTier = 'compact' | 'wide' | 'desktop';
export type PanelMode = 'default' | 'expanded' | 'collapsed';
export type Detent = 'collapsed' | 'half' | 'full';

export interface StageLayout {
  /** Side_Panel width in px; 0 on compact. */
  panelWidth: number;
  /** Inspector_Panel width in px; 0 unless open. */
  inspectorWidth: number;
  /** Clear map width: stageWidth minus panels, insets and the 12 px gaps (a lower bound of the edge-to-edge map). */
  mapWidth: number;
  /** True when expanding would still leave the Map_View at least 480 px. */
  canExpand: boolean;
}

export const COMPACT_MAX = 768; // compact: width < 768
export const DESKTOP_MIN = 1280;

export const GUTTER = 12;
/** Side_Panel inset from the stage's left edge (AppFrame wide frame, left-3). */
export const PANEL_LEFT = 12;
/** Inspector_Panel inset from the stage's right edge; keeps it clear of MapLibre's controls. */
export const INSPECTOR_RIGHT = 60;
export const MAP_MIN_WIDTH = 480;
export const PANEL_DEFAULT = 400;
export const PANEL_MIN = 360;
export const PANEL_EXPANDED_MAX = 720;
export const PANEL_RAIL = 72;
export const INSPECTOR_DEFAULT = 400;
export const INSPECTOR_MIN = 340;

export const DETENT_COLLAPSED = 88;
export const DETENT_HALF_MIN = 280;

const FLING_PROJECTION_MS = 150;

export function viewportTier(width: number): ViewportTier {
  if (width < COMPACT_MAX) return 'compact';
  if (width < DESKTOP_MIN) return 'wide';
  return 'desktop';
}

export function stageLayout(
  stageWidth: number,
  panelMode: PanelMode,
  inspectorOpen: boolean,
): StageLayout {
  const width = Math.max(0, stageWidth);
  // Fixed space: left inset + gap panel->map, plus (inspector open) gap map->inspector + right inset.
  const gutters = PANEL_LEFT + GUTTER + (inspectorOpen ? GUTTER + INSPECTOR_RIGHT : 0);
  const room = width - gutters;

  let inspector = inspectorOpen ? INSPECTOR_DEFAULT : 0;
  let panel = panelMode === 'collapsed' ? PANEL_RAIL : PANEL_DEFAULT;

  // Narrow the Side_Panel first, then the Inspector_Panel, to keep the map usable.
  let shortfall = MAP_MIN_WIDTH - (room - panel - inspector);
  if (shortfall > 0 && panel > PANEL_MIN && panelMode !== 'collapsed') {
    const cut = Math.min(shortfall, panel - PANEL_MIN);
    panel -= cut;
    shortfall -= cut;
  }
  if (shortfall > 0 && inspectorOpen) {
    inspector -= Math.min(shortfall, inspector - INSPECTOR_MIN);
  }

  // Largest panel that still leaves the map at its minimum, capped at 720.
  const expandedMax = Math.min(PANEL_EXPANDED_MAX, room - inspector - MAP_MIN_WIDTH);
  const canExpand = panelMode !== 'collapsed' && expandedMax > panel;
  if (panelMode === 'expanded' && canExpand) panel = expandedMax;

  return {
    panelWidth: panel,
    inspectorWidth: inspector,
    mapWidth: room - panel - inspector,
    canExpand,
  };
}

export interface DetentHeights {
  collapsed: number;
  half: number;
  full: number;
}

export function detentHeights(available: number): DetentHeights {
  const collapsed = DETENT_COLLAPSED;
  const full = available - 8;
  let half: number;
  if (available < 400) {
    half = (collapsed + full) / 2;
  } else {
    const upper = available - 96;
    half = Math.min(Math.max(available / 2, DETENT_HALF_MIN), upper);
  }
  return { collapsed, half, full };
}

const ORDER: readonly Detent[] = ['collapsed', 'half', 'full'];

/** Cycles Collapsed -> Half -> Full -> Collapsed. */
export function nextDetent(d: Detent): Detent {
  return ORDER[(ORDER.indexOf(d) + 1) % ORDER.length];
}

/**
 * Settles a drag. `y` is the sheet height (px) at release; `velocity` is in
 * px/ms with positive meaning the sheet is growing. The release position is
 * projected forward by the velocity, then the closest Detent wins.
 */
export function nearestDetent(heights: DetentHeights, y: number, velocity: number): Detent {
  const projected = y + (Number.isFinite(velocity) ? velocity : 0) * FLING_PROJECTION_MS;
  let best: Detent = 'collapsed';
  let bestDistance = Infinity;
  for (const d of ORDER) {
    const distance = Math.abs(heights[d] - projected);
    if (distance < bestDistance) {
      best = d;
      bestDistance = distance;
    }
  }
  return best;
}
