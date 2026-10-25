import { render, screen, within, type RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Incident, MeInfo, RouteResponse } from '../../services/api';
import type { RouteRecalcSnapshot } from '../../services/routeRecalc';
import type { UseFeatureInspector } from '../inspect/useFeatureInspector';
import type { CommunityMessage, CommunityReport } from '../community/types';
import { SCREEN_TAB, TAB_IDS, TAB_TITLES, type TabId } from './navigation';
import type { CommunityFeed } from './RightSidebar';
import { SCREEN_IDS, screenHash, type ScreenId } from './screens';
import { CommunityTab } from './tabs/CommunityTab';
import { MapTab } from './tabs/MapTab';
import { MoreTab } from './tabs/MoreTab';
import { RouteTab } from './tabs/RouteTab';
import { SafetyTab } from './tabs/SafetyTab';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const incident: Incident = {
  incident_id: 'flood-1', incident_type: 'flood', simulation_flag: true, status: 'active', source: 'demo',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

const report: CommunityReport = {
  report_id: 'r1', category: 'flooding', description: 'Water over the curb', created_at: '2025-01-01T12:00:00Z',
  alias: 'Heron-12', source_label: 'Community report', verification_status: 'Unverified', confirmations: 0, disputes: 0, simulated: false,
};
const msg = (over: Partial<CommunityMessage>): CommunityMessage => ({
  message_id: 'm1', message_type: 'update', content: 'Neighbors are gathering', created_at: '2025-01-01T12:00:00Z', alias: 'Otter-3',
  is_official: false, source_record_id: null, source_organization: null, source_url: null, ...over,
});
const feed: CommunityFeed = {
  reports: [report],
  messages: [
    msg({}),
    msg({ message_id: 'm2', content: 'Please help on 5th floor', message_type: 'assistance_request' }),
    msg({ message_id: 'm3', content: 'Road closed at Main', is_official: true, source_record_id: 'SR-1', source_organization: 'Chicago OEMC' }),
  ],
  checkins: [{ checkin_id: 'c1', status: 'safe', status_label: null, created_at: null, alias: 'Fox-1' }],
};

type R = RouteResponse['routes'][number];
const route = (id: string): R => ({
  calculated_at: '2025-01-01T00:00:00Z',
  confidence_limitations: ['Elevation data is low confidence'],
  distance_meters: 1500,
  edge_penalties: [],
  estimated_duration_seconds: 1080,
  excluded_edges_count: 7,
  geometry: { type: 'LineString', coordinates: [[-87.6, 41.8], [-87.61, 41.81]] },
  hazard_flags: [],
  label: 'algorithm-generated',
  policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
  reason_summary: `Reason for ${id}`,
  restrictions_considered: [],
  route_id: id,
  route_type: 'disaster-adjusted',
  sources: [],
  status: 'ok',
  uncertainty_level: 'Medium',
});
const routeResponse: RouteResponse = {
  calculated_at: '2025-01-01T00:00:00Z',
  data_timestamp: '2025-01-01T00:00:00Z',
  explanation: 'Explanation text.',
  label: 'algorithm-generated',
  missing_data: [],
  notices: [],
  policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
  route_count: 1,
  routes: [route('a')],
  sources: [],
  status: 'ok',
};
const recalc: RouteRecalcSnapshot = {
  route: null, previousRoute: null, change: 'The route is now 200 m shorter.', status: 'idle', updating: false,
  message: null, policyOutcome: null, stale: false, calculatedAt: null,
};

const me = { role: 'resident', display_name: 'Sam', is_guest: true, expires_at: null } as unknown as MeInfo;

const inspectorState: UseFeatureInspector = {
  candidates: [], selectedId: null, selected: null, loading: false, error: null, announcement: '', open: false,
  onMapClick: vi.fn(), inspectCoordinate: vi.fn(), select: vi.fn(), close: vi.fn(),
};

// ---------------------------------------------------------------------------
// Harness: every "open screen" callback writes the pushed-screen hash, as useNavigation.openScreen does.
// ---------------------------------------------------------------------------

const go = (id: ScreenId) => {
  window.location.hash = screenHash(id);
};

interface MountOpts {
  demoMode?: boolean;
  tier?: 'compact' | 'wide' | 'desktop';
}

function mount(tab: TabId, opts: MountOpts = {}): RenderResult {
  switch (tab) {
    case 'map':
      return render(
        <MapTab
          tier={opts.tier ?? 'wide'}
          incident={incident}
          onReportHazard={() => go('submit-report')}
          onRequestAssistance={() => go('community')}
          onMarkSafe={() => go('safety-check-in')}
          onViewGuidance={() => go('official-guidance')}
          disasterSelector={{
            selectedType: 'flood', incidents: [incident], selectedIncidentId: 'flood-1',
            onSelectType: vi.fn(), onSelectIncident: vi.fn(),
          }}
          onOpenScreen={go}
          // The real MapLayerControls (toggle switches + legend) is a separate task; MapTab only owns the slot.
          layerControls={<div data-testid="layer-controls">Map layers</div>}
          inspector={{ inspector: inspectorState }}
          indoor
          indoorProps={{ building: null }}
        />,
      );
    case 'route':
      return render(
        <RouteTab
          start={null}
          destination={null}
          pickMode={null}
          onPickModeChange={vi.fn()}
          onStartChange={vi.fn()}
          onDestinationChange={vi.fn()}
          showSamplePoints
          travelMode="walking"
          onTravelModeChange={vi.fn()}
          stepFree={false}
          onStepFreeChange={vi.fn()}
          shelter
          routePanel={{ response: routeResponse, recalc, onRecalculate: vi.fn() }}
          onOpenRouteDetails={() => go('route-details')}
        />,
      );
    case 'community':
      return render(<CommunityTab incidentId="flood-1" feed={feed} onOpenCommunications={() => go('community')} />);
    case 'safety':
      return render(
        <SafetyTab
          onReportHazard={() => go('submit-report')}
          onRequestAssistance={() => go('community')}
          onMarkSafe={() => go('safety-check-in')}
          onViewGuidance={() => go('official-guidance')}
        />,
      );
    case 'more':
      return render(
        <MoreTab
          me={me}
          demoMode={opts.demoMode}
          appearanceSetting="system"
          onAppearanceChange={vi.fn()}
          shortcutsEnabled
          onShortcutsChange={vi.fn()}
        />,
      );
  }
}

const opener = (name: string) => screen.queryByRole('link', { name }) ?? screen.getByRole('button', { name });

// ---------------------------------------------------------------------------
// Feature Parity Map data (design.md, "Feature_Inventory to tab" and "Tab contents")
// ---------------------------------------------------------------------------

/** The row or control on a screen's home tab that opens that screen. Exhaustive over ScreenId by type. */
const SCREEN_ROW: Record<ScreenId, string> = {
  'disaster-selection': 'Select a disaster',
  'route-details': 'Route details',
  'submit-report': 'Report a hazard',
  community: 'Community communications',
  'incident-overview': 'Incident overview',
  'safety-check-in': 'Mark safe',
  'official-guidance': 'View official guidance',
  'data-sources': 'Data sources and limitations',
};

interface InventoryEntry {
  item: string;
  /** The one tab that is the item's home. */
  home: TabId;
  /** Asserts the item is present in the already-mounted home tab. */
  check: () => unknown;
}

const user = () => userEvent.setup();
const testId = (id: string) => () => expect(screen.getByTestId(id)).toBeInTheDocument();

/**
 * Feature_Inventory table. "Safety check-in; assistance request; hazard report submission" has its actions on
 * Safety and its feeds on Community; the Safety action rows are the item's single primary home (design.md).
 */
const INVENTORY: readonly InventoryEntry[] = [
  { item: 'Active incident display', home: 'map', check: () => expect(within(screen.getByTestId('active-incident')).getByText('Active incident')).toBeInTheDocument() },
  { item: 'Disaster type selector', home: 'map', check: testId('disaster-selector') },
  { item: 'Incident selector', home: 'map', check: () => expect(within(screen.getByTestId('disaster-selector')).getByRole('button', { name: /flood-1/ })).toBeInTheDocument() },
  { item: 'Map layer toggles and legend', home: 'map', check: testId('layer-controls') },
  { item: 'Map feature inspector', home: 'map', check: testId('inspect-live') },
  { item: 'Indoor building view', home: 'map', check: () => { testId('indoor-area')(); testId('indoor-view')(); } },
  { item: 'Start and destination inputs', home: 'route', check: () => { expect(document.querySelector('#start-lat')).not.toBeNull(); expect(document.querySelector('#dest-lng')).not.toBeNull(); } },
  { item: 'Map pick mode', home: 'route', check: () => expect(screen.getByRole('button', { name: 'Pick start on map' })).toBeInTheDocument() },
  { item: 'Sample points', home: 'route', check: () => expect(screen.getByRole('button', { name: 'Use sample points (flood scenario)' })).toBeInTheDocument() },
  { item: 'Travel mode', home: 'route', check: () => expect(screen.getByRole('radiogroup', { name: 'Travel mode' })).toBeInTheDocument() },
  { item: 'Step-free option', home: 'route', check: () => expect(screen.getByRole('switch', { name: 'Step-free route' })).toBeInTheDocument() },
  { item: 'Route alternatives and selection', home: 'route', check: testId('route-item') },
  { item: 'Route details', home: 'route', check: () => expect(screen.getByRole('button', { name: 'Route details' })).toBeInTheDocument() },
  { item: 'Route uncertainty summary', home: 'route', check: testId('route-uncertainty') },
  { item: 'Route change banner', home: 'route', check: testId('route-change-banner') },
  { item: 'Recalculate', home: 'route', check: () => expect(screen.getByRole('button', { name: 'Recalculate route' })).toBeInTheDocument() },
  {
    item: 'Hazard report list with corroborate and dispute',
    home: 'community',
    check: () => {
      expect(screen.getByTestId('report-card')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Corroborate' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Dispute' })).toBeInTheDocument();
    },
  },
  {
    item: 'Community messages and official notices',
    home: 'community',
    check: async () => {
      await user().click(screen.getByRole('tab', { name: /Messages & notices/ }));
      expect(screen.getByTestId('official-notices')).toBeInTheDocument();
      expect(screen.getAllByTestId('community-message').length).toBeGreaterThan(0);
    },
  },
  { item: 'Safety check-in', home: 'safety', check: () => expect(screen.getByRole('button', { name: 'Mark safe' })).toBeInTheDocument() },
  { item: 'Assistance request', home: 'safety', check: () => expect(screen.getByRole('button', { name: 'Request assistance' })).toBeInTheDocument() },
  { item: 'Hazard report submission', home: 'safety', check: () => expect(screen.getByRole('button', { name: 'Report a hazard' })).toBeInTheDocument() },
  { item: 'Official guidance', home: 'safety', check: testId('official-guidance') },
  { item: 'Emergency 911 guidance', home: 'safety', check: () => expect(screen.getByTestId('safety-911-card')).toHaveTextContent('call 911 from your phone') },
  { item: 'Incident overview', home: 'more', check: () => expect(screen.getByRole('link', { name: 'Incident overview' })).toBeInTheDocument() },
  { item: 'Data sources and limitations', home: 'more', check: () => expect(screen.getByRole('link', { name: 'Data sources and limitations' })).toBeInTheDocument() },
  { item: 'Official links', home: 'more', check: testId('official-links') },
  { item: 'Profile', home: 'more', check: () => { testId('more-account')(); expect(screen.getByText('Sam')).toBeInTheDocument(); } },
  { item: 'Appearance', home: 'more', check: () => expect(screen.getByRole('radiogroup', { name: 'Appearance' })).toBeInTheDocument() },
  { item: 'Demo admin console', home: 'more', check: () => expect(screen.getByRole('link', { name: 'Demo console' })).toBeInTheDocument() },
];

// Connectivity status and the offline status / report queue live in the Header and banner on every tab,
// so they are deliberately not part of any tab's inventory (design.md, Feature_Inventory to tab).

beforeEach(() => {
  window.location.hash = '';
});

// ---------------------------------------------------------------------------
// Tab content (Feature Parity Map)
// ---------------------------------------------------------------------------

describe('Feature Parity Map: tab content', () => {
  it.each(INVENTORY.map((e) => [e.home, e.item, e] as const))('%s tab contains "%s"', async (home, _item, entry) => {
    mount(home, { demoMode: true });
    await entry.check();
  });

  it('Map tab shows four quick actions (no recalculate) and a Select a disaster row', () => {
    mount('map');
    const quick = screen.getByRole('navigation', { name: 'Quick actions' });
    expect(within(quick).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Report a hazard', 'Request assistance', 'Mark safe', 'View official guidance',
    ]);
    expect(screen.queryByRole('button', { name: /Recalculate/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Select a disaster' })).toBeInTheDocument();
  });

  it('Map tab renders the feature inspector below the desktop tier only', () => {
    const { unmount } = mount('map', { tier: 'desktop' });
    expect(screen.queryByTestId('inspect-live')).toBeNull();
    unmount();
    mount('map', { tier: 'compact' });
    expect(screen.getByTestId('inspect-live')).toBeInTheDocument();
  });

  it('Community tab offers the three segments and the Community communications row', () => {
    mount('community');
    for (const name of [/Reports/, /Messages & notices/, /Check-ins & assistance/]) {
      expect(screen.getByRole('tab', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Community communications' })).toBeInTheDocument();
  });

  it('Community tab shows the check-in and assistance feeds', async () => {
    mount('community');
    await user().click(screen.getByRole('tab', { name: /Check-ins & assistance/ }));
    expect(screen.getByTestId('assistance-requests')).toHaveTextContent('Please help on 5th floor');
  });

  it('Safety tab lists the four action rows before the 911 Card and guidance', () => {
    mount('safety');
    const names = ['Report a hazard', 'Request assistance', 'Mark safe', 'View official guidance'];
    const list = screen.getByRole('region', { name: 'Safety actions' });
    expect(within(list).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(names);
  });

  it('More tab has the settings group with Appearance and the Keyboard shortcuts switch', () => {
    mount('more');
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Keyboard shortcuts' })).toBeInTheDocument();
    expect(screen.getByTestId('shortcut-list')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Demo console entry (demo mode only)
// ---------------------------------------------------------------------------

describe('Demo console entry', () => {
  it('is hidden when demo mode is off', () => {
    mount('more', { demoMode: false });
    expect(screen.queryByRole('link', { name: 'Demo console' })).toBeNull();
    expect(screen.queryByText('Demo console')).toBeNull();
  });

  it('is hidden when demo mode is not provided', () => {
    mount('more');
    expect(screen.queryByText('Demo console')).toBeNull();
  });

  it('is shown on the More tab and opens #/demo when demo mode is on', async () => {
    mount('more', { demoMode: true });
    const row = screen.getByRole('link', { name: 'Demo console' });
    expect(row).toHaveAttribute('href', '#/demo');
    await user().click(row);
    expect(window.location.hash).toBe('#/demo');
  });

  it('never appears on the other four tabs', () => {
    for (const tab of TAB_IDS.filter((t) => t !== 'more')) {
      const { unmount } = mount(tab, { demoMode: true });
      expect(screen.queryByText('Demo console')).toBeNull();
      unmount();
    }
  });
});

// ---------------------------------------------------------------------------
// Screen rows open the matching screen hash
// ---------------------------------------------------------------------------

describe('Screen rows', () => {
  it.each(SCREEN_IDS.map((id) => [id, SCREEN_TAB[id]] as const))('the %s row on the %s tab opens its screen hash', async (id, home) => {
    mount(home);
    await user().click(opener(SCREEN_ROW[id]));
    expect(window.location.hash).toBe(screenHash(id));
  });

  it('every More tab screen row is a link whose href is the screen hash', () => {
    mount('more');
    for (const id of SCREEN_IDS.filter((s) => SCREEN_TAB[s] === 'more')) {
      expect(screen.getByRole('link', { name: SCREEN_ROW[id] })).toHaveAttribute('href', screenHash(id));
    }
  });

  it('Map tab quick actions are shortcuts to the same screens as the Safety rows', async () => {
    const expected: Record<string, ScreenId> = {
      'Report a hazard': 'submit-report',
      'Request assistance': 'community',
      'Mark safe': 'safety-check-in',
      'View official guidance': 'official-guidance',
    };
    for (const [name, id] of Object.entries(expected)) {
      const { unmount } = mount('map');
      await user().click(within(screen.getByRole('navigation', { name: 'Quick actions' })).getByRole('button', { name }));
      expect(window.location.hash).toBe(screenHash(id));
      unmount();
    }
  });
});

// ---------------------------------------------------------------------------
// Property 5
// ---------------------------------------------------------------------------

// Feature: ios-style-ui-redesign, Property 5: Every screen and Feature_Inventory item has exactly one home tab
// **Validates: Requirements 2.1, 2.2, 6.4, 6.9**
describe('Property 5: Every screen and Feature_Inventory item has exactly one home tab', () => {
  it('SCREEN_TAB assigns every ScreenId to exactly one of the five tabs', () => {
    expect(TAB_IDS).toHaveLength(5);
    expect(Object.keys(SCREEN_TAB).sort()).toEqual([...SCREEN_IDS].sort());
    fc.assert(
      fc.property(fc.constantFrom(...SCREEN_IDS), (id) => {
        const homes = TAB_IDS.filter((t) => SCREEN_TAB[id] === t);
        expect(homes).toHaveLength(1);
      }),
      { numRuns: 100 },
    );
  });

  it('every ScreenId has a row on its home tab that opens it (exhaustive over SCREEN_IDS)', async () => {
    for (const id of SCREEN_IDS) {
      window.location.hash = '';
      const { unmount } = mount(SCREEN_TAB[id]);
      await user().click(opener(SCREEN_ROW[id]));
      expect(window.location.hash, `screen ${id} on tab ${SCREEN_TAB[id]}`).toBe(screenHash(id));
      unmount();
    }
  });

  it('every Feature_Inventory item is assigned to exactly one tab', () => {
    fc.assert(
      fc.property(fc.constantFrom(...INVENTORY), (entry) => {
        const rows = INVENTORY.filter((e) => e.item === entry.item);
        expect(rows).toHaveLength(1);
        expect(TAB_IDS.filter((t) => t === rows[0].home)).toHaveLength(1);
      }),
      { numRuns: 100 },
    );
    expect(new Set(INVENTORY.map((e) => e.item)).size).toBe(INVENTORY.length);
  });

  it('every Feature_Inventory item is present in its home tab (exhaustive over the table)', async () => {
    for (const entry of INVENTORY) {
      const { unmount } = mount(entry.home, { demoMode: true });
      await entry.check();
      unmount();
    }
  });

  it('every tab is the home of at least one inventory item and one tab title exists per tab', () => {
    for (const tab of TAB_IDS) {
      expect(INVENTORY.some((e) => e.home === tab)).toBe(true);
      expect(TAB_TITLES[tab]).toBeTruthy();
    }
  });
});
