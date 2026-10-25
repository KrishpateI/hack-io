import {
  useLayoutEffect,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { MapView } from '../../maps/MapView';
import { BottomSheet } from '../ios/BottomSheet';
import { useViewportTier } from '../ios/hooks';
import { InspectorPanel } from '../ios/InspectorPanel';
import { detentHeights, GUTTER, INSPECTOR_RIGHT, stageLayout, type Detent } from '../ios/layout';
import { PanelBody, PanelSlot } from '../ios/PanelBody';
import { SidePanel, useStoredPanelMode } from '../ios/SidePanel';
import { TabBar } from '../ios/TabBar';
import { TabList } from '../ios/TabList';
import { selectedTab, TAB_IDS, TAB_TITLES, type TabId } from './navigation';
import { SCREEN_TITLES, type ScreenId } from './screens';
import { useNavigation } from './useNavigation';

/** Height (px) of the attribution strip between the Bottom_Sheet and the Tab_Bar (Compact). */
export const ATTRIBUTION_STRIP_HEIGHT = 24;
/** Tab_Bar height used until the real one is measured (jsdom has no layout). */
const TAB_BAR_FALLBACK = 56;
/** Right inset that keeps overlays clear of MapLibre's navigation control. */
const MAP_CONTROL_INSET = 56;

export type PickMode = 'start' | 'destination' | null;

/** What a pushed screen needs from the frame. Passed to `renderScreen`. */
export interface ScreenRenderContext {
  screen: ScreenId | null;
  demo: boolean;
  /** Closes the screen and returns to its parent tab. */
  onClose: () => void;
  /** Goes back one step (history back, or the parent tab after a deep link). */
  onBack: () => void;
  /** Title of the previous location, for the `Back to <title>` control. */
  backLabel: string;
}

export interface InspectorSlot {
  open: boolean;
  /** Heading of the inspected feature. */
  title: string;
  onClose: () => void;
  /** Content shown in the Inspector_Panel at desktop. Below desktop the Map tab shows the inspector. */
  content: ReactNode;
}

export interface AppFrameProps {
  /** Props passed to `MapView` (layer inputs, click handler). Padding is supplied by the frame. */
  mapProps?: Omit<ComponentProps<typeof MapView>, 'viewPadding'>;
  /** Content of each tab. Only the active tab is called, so only it is mounted. */
  renderTab: Record<TabId, () => ReactNode>;
  /** Content of the pushed screen or the demo console. Called only while one is open. */
  renderScreen: (ctx: ScreenRenderContext) => ReactNode;
  /** Active pick mode: lowers the Bottom_Sheet and shows the pick hint. */
  pickMode?: PickMode;
  /** Cancels pick mode (the hint's Cancel button). */
  onCancelPick?: () => void;
  /** A route is computed: raises a collapsed Bottom_Sheet to Half. */
  routeReady?: boolean;
  /** A RouteChangeBanner currently renders: the Route tab shows "Updated" while off-tab. */
  routeChanged?: boolean;
  /** Feature inspector. `open` raises the sheet and brings the Map tab forward below desktop. */
  inspector?: InspectorSlot;
}

interface Size {
  w: number;
  h: number;
}

/** Measures an element; falls back to the window where there is no layout (jsdom). */
function useElementSize(el: HTMLElement | null, fallback: () => Size): Size {
  const [size, setSize] = useState<Size>({ w: 0, h: 0 });
  const fallbackRef = useRef(fallback);
  useEffect(() => {
    fallbackRef.current = fallback;
  });
  useLayoutEffect(() => {
    if (!el) return undefined;
    const measure = () => {
      const r = el.getBoundingClientRect();
      const fb = fallbackRef.current();
      const next = { w: r.width || fb.w, h: r.height || fb.h };
      setSize((prev) => (prev.w === next.w && prev.h === next.h ? prev : next));
    };
    measure();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    ro?.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [el]);
  return size;
}

const stageFallback = (): Size => ({
  w: window.innerWidth,
  h: Math.max(448, window.innerHeight - 160),
});

/** Hint shown over the map while a start or destination is being picked. */
function PickHint({
  pickMode,
  onCancel,
  left,
  right,
}: {
  pickMode: 'start' | 'destination';
  onCancel?: () => void;
  left: number;
  right: number;
}) {
  return (
    <div
      className="pointer-events-none absolute top-3 z-30 flex justify-center"
      style={{ left, right }}
    >
      <p
        role="status"
        data-testid="pick-hint"
        className="glass pointer-events-auto m-0 flex max-w-full items-center gap-3 rounded-[var(--radius-control)] border border-route px-3 py-2 text-[length:var(--text-body)] text-route shadow-[var(--shadow-card)]"
      >
        <span>Click the map to set the {pickMode}. Press the pick button again to cancel.</span>
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            className="min-h-[var(--target-min)] shrink-0 rounded-[var(--radius-control)] border border-route px-3 font-semibold"
          >
            Cancel
          </button>
        ) : null}
      </p>
    </div>
  );
}

/**
 * The application frame: the stage (map plus floating panels). Wide tiers get a Side_Panel with a
 * Tab_List (and an Inspector_Panel at desktop); Compact gets a Bottom_Sheet with a Tab_Bar. The
 * panel content renders once through `PanelBody`, so tab, Detent, selection and typed form values
 * survive a change of tier. The Disclaimer_Banner, header, and offline banner are rendered by the
 * caller above the stage and are never covered by it.
 */
export function AppFrame({
  mapProps,
  renderTab,
  renderScreen,
  pickMode = null,
  onCancelPick,
  routeReady = false,
  routeChanged = false,
  inspector,
}: AppFrameProps) {
  const tier = useViewportTier();
  const nav = useNavigation();
  const { tab, screen, demo, openTab } = nav;
  const screenOpen = screen !== null || demo;
  const compact = tier === 'compact';
  const desktop = tier === 'desktop';
  const inspectorOpen = inspector?.open ?? false;

  const [stageEl, setStageEl] = useState<HTMLDivElement | null>(null);
  const [bottomEl, setBottomEl] = useState<HTMLDivElement | null>(null);
  const mapRegion = useRef<HTMLDivElement>(null);
  const stage = useElementSize(stageEl, stageFallback);
  const bottom = useElementSize(bottomEl, () => ({
    w: window.innerWidth,
    h: ATTRIBUTION_STRIP_HEIGHT + TAB_BAR_FALLBACK,
  }));

  const [panelMode, setPanelMode] = useStoredPanelMode();

  // Detent survives tier changes. Content arriving raises a collapsed sheet to Half; pick mode
  // lowers it to Collapsed so the map is clear (Requirements 7.13, 7.15).
  const [detent, setDetent] = useState<Detent>('collapsed');
  const [seen, setSeen] = useState({ routeReady, inspectorOpen, pickMode, screenOpen });
  if (
    seen.routeReady !== routeReady ||
    seen.inspectorOpen !== inspectorOpen ||
    seen.pickMode !== pickMode ||
    seen.screenOpen !== screenOpen
  ) {
    const pickStarted = pickMode !== null && seen.pickMode === null;
    const arrived =
      (routeReady && !seen.routeReady) ||
      (inspectorOpen && !seen.inspectorOpen) ||
      (screenOpen && !seen.screenOpen);
    setSeen({ routeReady, inspectorOpen, pickMode, screenOpen });
    if (pickStarted) setDetent('collapsed');
    else if (arrived && pickMode === null) setDetent((d) => (d === 'collapsed' ? 'half' : d));
  }

  // Below desktop the inspector lives in the Map tab, so bring that tab forward on selection.
  const prevInspectorOpen = useRef(inspectorOpen);
  useEffect(() => {
    const opened = inspectorOpen && !prevInspectorOpen.current;
    prevInspectorOpen.current = inspectorOpen;
    if (opened && !desktop && pickMode === null && !screenOpen && tab !== 'map') openTab('map');
  }, [inspectorOpen, desktop, pickMode, screenOpen, tab, openTab]);

  const showUpdated = routeChanged && tab !== 'route';
  const tabs = useMemo(
    () =>
      TAB_IDS.map((id) => ({
        id,
        label: TAB_TITLES[id],
        icon: id,
        badge: id === 'route' && showUpdated ? 'Updated' : undefined,
      })),
    [showUpdated],
  );

  const layout = stageLayout(stage.w, panelMode, desktop && inspectorOpen);
  const bottomHeight = bottom.h;
  const tabBarHeight = Math.max(0, bottomHeight - ATTRIBUTION_STRIP_HEIGHT);
  const available = Math.max(0, stage.h - bottomHeight);

  const padLeft = compact ? 0 : layout.panelWidth + GUTTER;
  const padRight = desktop && inspectorOpen ? layout.inspectorWidth + INSPECTOR_RIGHT : 0;
  const viewPadding = compact
    ? { bottom: detentHeights(available)[detent] + bottomHeight }
    : { left: padLeft, right: padRight };

  const panelTitle = screen ? SCREEN_TITLES[screen] : demo ? 'Demo console' : TAB_TITLES[tab];
  const onClose = () => openTab(selectedTab(nav.location));
  const onSelectTab = (id: TabId) => {
    openTab(id);
    if (compact) setDetent((d) => (d === 'collapsed' ? 'half' : d));
  };

  const content: ReactNode = (
    <>
      {screenOpen
        ? renderScreen({ screen, demo, onClose, onBack: nav.back, backLabel: nav.backLabel })
        : null}
      <div hidden={screenOpen}>{renderTab[tab]()}</div>
    </>
  );

  const wideFrame = (
    <div className="absolute bottom-3 left-3 top-3 z-20 flex" data-testid="wide-frame">
      <SidePanel
        mode={panelMode}
        onModeChange={setPanelMode}
        layout={layout}
        nav={
          <TabList tabs={tabs} current={tab} onSelect={onSelectTab} rail={panelMode === 'collapsed'} />
        }
      >
        <PanelSlot title={panelTitle} />
      </SidePanel>
    </div>
  );

  const compactFrame = (
    <>
      <div
        className="pointer-events-none absolute inset-x-0 top-0 z-20 flex flex-col justify-end"
        style={{ bottom: bottomHeight }}
      >
        <BottomSheet
          detent={detent}
          onDetentChange={setDetent}
          available={available}
          mapRef={mapRegion}
          className="pointer-events-auto"
        >
          <div className="p-3">
            <PanelSlot title={panelTitle} />
          </div>
        </BottomSheet>
      </div>
      <div ref={setBottomEl} className="absolute inset-x-0 bottom-0 z-30" data-testid="compact-bottom">
        <div
          aria-hidden="true"
          data-testid="attribution-strip"
          className="pointer-events-none"
          style={{ height: ATTRIBUTION_STRIP_HEIGHT }}
        />
        <TabBar tabs={tabs} current={tab} onSelect={onSelectTab} />
      </div>
    </>
  );

  return (
    <div
      ref={setStageEl}
      data-testid="dashboard"
      data-viewport-tier={tier}
      className="relative min-w-0 flex-1 overflow-hidden"
      style={{ minHeight: '28rem' }}
    >
      <div
        ref={mapRegion}
        role="region"
        aria-label="Map area"
        tabIndex={-1}
        data-focus-target="map"
        className="absolute inset-0 focus:outline-none"
        style={
          { '--map-attribution-bottom': compact ? `${tabBarHeight}px` : '0px' } as CSSProperties
        }
      >
        <MapView {...mapProps} viewPadding={viewPadding} />
      </div>

      <PanelBody frame={compact ? compactFrame : wideFrame}>{content}</PanelBody>

      {desktop && inspector ? (
        <InspectorPanel
          open={inspector.open}
          title={inspector.title}
          onClose={inspector.onClose}
          width={layout.inspectorWidth || undefined}
        >
          {inspector.content}
        </InspectorPanel>
      ) : null}

      {pickMode ? (
        <PickHint
          pickMode={pickMode}
          onCancel={onCancelPick}
          left={compact ? GUTTER : padLeft + GUTTER}
          right={padRight ? padRight + GUTTER : MAP_CONTROL_INSET}
        />
      ) : null}

      <div aria-live="polite" role="status" className="sr-only" data-testid="frame-announcer">
        {showUpdated ? 'Route updated. See the Route tab.' : ''}
      </div>
    </div>
  );
}
