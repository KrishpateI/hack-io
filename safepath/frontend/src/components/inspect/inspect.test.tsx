import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import type { SourceRecord } from '../../services/sources';
import { attachInspector, parseProperties, type InspectClickEvent, type InspectMapLike } from './attachInspector';
import { edgeUrl, edgesNearUrl, type EdgeDetail } from './edgeService';
import { FeatureInspector } from './FeatureInspector';
import {
  buildCandidates,
  buildingSummary,
  formatElevation,
  formatFlag,
  hazardSummary,
  kindOfLayer,
  roadRows,
  sourceCategory,
  stationSummary,
  verificationText,
} from './inspectModel';
import { useFeatureInspector } from './useFeatureInspector';

const EDGE: EdgeDetail = {
  edge_id: 'e1',
  street_name: 'Unknown',
  road_type: 'footway',
  bridge_flag: true,
  tunnel_flag: 'Unknown',
  infrastructure_level: 'bridge',
  pedestrian_access: true,
  mobility_accessibility: 'Unknown',
  elevation_start: 182.34,
  elevation_end: 'Unknown',
  elevation_confidence: 'Low',
  current_closure_status: 'closed',
  source: 'OSM+Cook County',
  source_ids: { segment_id: 'seg-9', osm: 'SRC-OSM' },
  data_timestamp: '2026-01-01T12:30:00Z',
  classification: 'Simulated',
  restrictions: {
    incident_id: 'inc1',
    available: true,
    causes: [{ kind: 'closure', id: 'ovl-1', source_record_id: 'SRC-OSM', simulated: true }],
  },
  reports: [
    { report_id: 'r1', category: 'flooding', description: 'water on road', verification_status: 'Corroborated' },
  ],
};

const SOURCE: SourceRecord = {
  source_id: 'SRC-OSM',
  organization: 'OpenStreetMap contributors',
  url: 'https://www.openstreetmap.org',
  title: 'OSM extract',
  retrieved_at: '2026-01-01T00:00:00Z',
  data_classification: 'Static',
  verification_status: 'unknown',
  is_official_source: false,
};
const CBD: SourceRecord = {
  ...SOURCE,
  source_id: 'CBD-1',
  organization: 'City of Chicago',
  title: 'CBD evacuation plan',
  data_classification: 'Historical',
  is_official_source: true,
};

describe('formatters', () => {
  it('renders missing values as Unknown and elevations with units', () => {
    expect(formatElevation(182.34)).toBe('182.3 m NAVD88');
    expect(formatElevation('Unknown')).toBe('Unknown');
    expect(formatElevation(null)).toBe('Unknown');
    expect(formatFlag(true)).toBe('Yes');
    expect(formatFlag(false)).toBe('No');
    expect(formatFlag('Unknown')).toBe('Unknown');
  });

  it('shows every road attribute, never blank', () => {
    const rows = Object.fromEntries(roadRows({ edge_id: 'x' }));
    for (const [k, v] of Object.entries(rows)) expect(v, k).toBeTruthy();
    expect(rows['Road name']).toBe('Unknown');
    expect(rows['Elevation at start']).toBe('Unknown');
    expect(rows['One-way']).toBe('Unknown');
    expect(rows['Last update']).toBe('Unknown');
  });

  it('property: any non-number elevation is Unknown, any finite number carries the unit', () => {
    fc.assert(
      fc.property(fc.oneof(fc.string(), fc.constant(null), fc.boolean()), (v) => formatElevation(v) === 'Unknown'),
    );
    fc.assert(fc.property(fc.double({ min: -50, max: 500, noNaN: true }), (n) => formatElevation(n).endsWith(' m NAVD88')));
  });

  it('uses the exact Corroborated wording and the three source categories', () => {
    expect(verificationText('Corroborated', false)).toBe('Corroborated by community \u2014 not officially verified');
    expect(sourceCategory('Unverified', false)).toBe('Community report');
    expect(sourceCategory('Officially_Verified', false)).toBe('Official');
    expect(sourceCategory('Officially_Verified', true)).toBe('Simulated moderator verification');
  });

  it('summarizes hazards, buildings and stations without inferring anything', () => {
    const h = hazardSummary(
      { report_id: 'r1', description: 'd', confirmations: 3, created_at: '2026-01-01T00:00:00Z' },
      { report_id: 'r1', verification_status: 'Corroborated', expires_at: '2026-01-01T06:00:00Z', confirmations: 3 },
    );
    expect(h.corroborating).toBe(3);
    expect(h.expiry).toBe('2026-01-01 06:00:00 UTC');
    expect(h.sourceCategory).toBe('Community report');
    const b = buildingSummary({});
    expect(b).toMatchObject({ heightClass: 'Unknown', height: 'Unknown', levels: 'Unknown', structural: 'structural condition: Unknown' });
    expect(buildingSummary({ building_height_class: 'Mid-rise', height_m: 20, height_source: 'OSM height' }).height).toBe('20 m (source: OSM height)');
    expect(stationSummary({ name: 'Clark/Lake', routes: ['Blue', 'Green'] })).toMatchObject({
      routes: 'Blue, Green', entranceSafety: 'entrance safety Unknown',
    });
  });
});

describe('candidates', () => {
  it('orders point features before routes and buildings, and de-duplicates', () => {
    const cs = buildCandidates({
      lngLat: [-87.63, 41.88],
      hits: [
        { layerId: 'buildings-fill', properties: { name: 'B' } },
        { layerId: 'routes-primary', properties: { route_id: 'rt1', label: 'Route' } },
        { layerId: 'routes-casing', properties: { route_id: 'rt1', label: 'Route' } },
        { layerId: 'reports-symbol', properties: { report_id: 'r1' } },
        { layerId: 'unknown-layer', properties: {} },
      ],
    });
    expect(cs.map((c) => c.kind)).toEqual(['report', 'route', 'building']);
    expect(kindOfLayer('flood-fill')?.kind).toBe('hazard_zone');
  });

  it('parseProperties restores JSON stringified by MapLibre', () => {
    expect(parseProperties({ routes: '["Blue","Pink"]', name: 'x', odd: '[not json' })).toEqual({
      routes: ['Blue', 'Pink'], name: 'x', odd: '[not json',
    });
  });
});

describe('attachInspector', () => {
  it('queries only existing interactive layers and detaches', () => {
    let handler: ((e: InspectClickEvent) => void) | null = null;
    const query = vi.fn(() => [{ layer: { id: 'cta-stations-circle' }, properties: { name: 'S', routes: '["Blue"]' } }]);
    const map: InspectMapLike = {
      on: (_t, fn) => { handler = fn; },
      off: () => { handler = null; },
      getLayer: (id) => (id === 'cta-stations-circle' ? {} : undefined),
      queryRenderedFeatures: query,
    };
    const got = vi.fn();
    const detach = attachInspector(map, got);
    handler!({ point: { x: 1, y: 2 }, lngLat: { lng: -87.6, lat: 41.9 } });
    expect(query).toHaveBeenCalledWith({ x: 1, y: 2 }, { layers: ['cta-stations-circle'] });
    expect(got).toHaveBeenCalledWith({
      lngLat: [-87.6, 41.9],
      hits: [{ layerId: 'cta-stations-circle', properties: { name: 'S', routes: ['Blue'] } }],
    });
    detach();
    expect(handler).toBeNull();
  });
});

describe('edge service urls', () => {
  it('builds public endpoint urls', () => {
    expect(edgeUrl('a/b', 'inc1')).toBe('/api/graph/edges/a%2Fb?incident_id=inc1');
    expect(edgesNearUrl(41.88, -87.63, 25, 5)).toBe('/api/graph/edges/near?lat=41.88&lon=-87.63&radius=25&limit=5');
  });
});

describe('useFeatureInspector', () => {
  it('shows map features at once, then appends fetched roads', async () => {
    let resolve!: (e: EdgeDetail[]) => void;
    const fetchEdges = vi.fn(() => new Promise<EdgeDetail[]>((r) => { resolve = r; }));
    const { result } = renderHook(() => useFeatureInspector({ fetchEdges, incidentId: 'inc1' }));
    act(() => result.current.onMapClick({
      lngLat: [-87.63, 41.88],
      hits: [{ layerId: 'buildings-fill', properties: { name: 'B' } }],
    }));
    expect(result.current.open && result.current.loading).toBe(true);
    expect(result.current.selected?.kind).toBe('building');
    expect(fetchEdges).toHaveBeenCalledWith(41.88, -87.63, expect.objectContaining({ incidentId: 'inc1' }));
    await act(async () => resolve([EDGE]));
    expect(result.current.loading).toBe(false);
    expect(result.current.candidates.map((c) => c.kind)).toEqual(['building', 'road']);
    expect(result.current.selected?.kind).toBe('building');
    act(() => result.current.select('road:e1'));
    expect(result.current.selected?.kind).toBe('road');
    act(() => result.current.close());
    expect(result.current.open).toBe(false);
  });

  it('keeps map results and reports an error when the road lookup fails', async () => {
    const fetchEdges = vi.fn(() => Promise.reject(new Error('down')));
    const { result } = renderHook(() => useFeatureInspector({ fetchEdges }));
    act(() => result.current.onMapClick({ lngLat: [0, 0], hits: [{ layerId: 'reports-symbol', properties: { report_id: 'r' } }] }));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.candidates).toHaveLength(1);
  });
});

function Harness(props: { edges?: EdgeDetail[]; click?: Parameters<ReturnType<typeof useFeatureInspector>['onMapClick']>[0]; extra?: Partial<React.ComponentProps<typeof FeatureInspector>> }) {
  const inspector = useFeatureInspector({ fetchEdges: () => Promise.resolve(props.edges ?? []) });
  return (
    <div>
      <button type="button" onClick={() => inspector.onMapClick(props.click ?? { lngLat: [-87.63, 41.88], hits: [] })}>
        map click
      </button>
      <FeatureInspector inspector={inspector} {...props.extra} />
    </div>
  );
}

describe('FeatureInspector', () => {
  it('road: attributes, caveat, closure causes with SIMULATED, reports, source and Escape to close', async () => {
    const user = userEvent.setup();
    render(<Harness edges={[EDGE]} extra={{ sources: [SOURCE] }} />);
    await user.click(screen.getByRole('button', { name: 'map click' }));
    const panel = await screen.findByTestId('feature-inspector');
    await waitFor(() => expect(within(panel).getByTestId('inspect-road')).toBeInTheDocument());
    expect(panel).toHaveFocus();
    expect(screen.getByTestId('inspect-live')).toHaveAttribute('aria-live', 'polite');
    const road = within(panel).getByTestId('inspect-road');
    expect(road).toHaveTextContent('Road nameUnknown');
    expect(road).toHaveTextContent('182.3 m NAVD88');
    expect(road).toHaveTextContent('Elevation at endUnknown');
    expect(within(panel).getByTestId('elevation-caveat')).toHaveTextContent('terrain elevation, not bridge deck/tunnel level');
    expect(road).toHaveTextContent('Cause: closure');
    expect(within(road).getAllByTestId('simulated-badge').length).toBeGreaterThan(0);
    expect(road).toHaveTextContent('Corroborated by community \u2014 not officially verified');
    expect(road).toHaveTextContent('water on road');
    expect(road).toHaveTextContent('2026-01-01 12:30:00 UTC');
    await user.click(within(road).getAllByRole('button', { name: 'Source SRC-OSM' })[0]);
    expect(within(panel).getByTestId('source-popup')).toHaveTextContent('OpenStreetMap contributors');
    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('feature-inspector')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'map click' })).toHaveFocus();
  });

  it('hazard report: description, times, corroboration count, source category, expiry', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        click={{ lngLat: [0, 0], hits: [{ layerId: 'reports-symbol', properties: { report_id: 'r1', description: 'smoke', category: 'fire or smoke', created_at: '2026-01-01T00:00:00Z', confirmations: 2 } }] }}
        extra={{ reports: [{ report_id: 'r1', verification_status: 'Corroborated', expires_at: '2026-01-01T06:00:00Z', confirmations: 2 }] }}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'map click' }));
    const hz = await screen.findByTestId('inspect-hazard');
    expect(hz).toHaveTextContent('smoke');
    expect(hz).toHaveTextContent('Corroborating reports2');
    expect(hz).toHaveTextContent('Source categoryCommunity report');
    expect(hz).toHaveTextContent('Expires2026-01-01 06:00:00 UTC');
    expect(hz).toHaveTextContent('Corroborated by community \u2014 not officially verified');
  });

  it('building and station: Unknown structural and entrance safety', async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <Harness click={{ lngLat: [0, 0], hits: [{ layerId: 'buildings-fill', properties: {} }] }} />,
    );
    await user.click(screen.getByRole('button', { name: 'map click' }));
    const b = await screen.findByTestId('inspect-building');
    expect(b).toHaveTextContent('structural condition: Unknown');
    expect(b).toHaveTextContent('Building_Height_ClassUnknown');
    unmount();
    render(<Harness click={{ lngLat: [0, 0], hits: [{ layerId: 'cta-stations-circle', properties: { name: 'Clark/Lake', routes: ['Blue'] } }] }} />);
    await user.click(screen.getByRole('button', { name: 'map click' }));
    const s = await screen.findByTestId('inspect-station');
    expect(s).toHaveTextContent('Clark/Lake');
    expect(s).toHaveTextContent('entrance safety Unknown');
  });

  it('route click renders the route details; official layer shows the Source_Record with the historical label', async () => {
    const user = userEvent.setup();
    const route = {
      route_id: 'rt1', route_type: 'primary', label: 'Algorithm-generated route', distance_meters: 1200,
      estimated_duration_seconds: 900, reason_summary: 'Shorter than alternatives', restrictions_considered: [],
      excluded_edges_count: 0, confidence_limitations: ['Elevation is approximate'], sources: [],
      uncertainty_level: 'Low',
    } as never;
    const { unmount } = render(
      <Harness click={{ lngLat: [0, 0], hits: [{ layerId: 'routes-primary', properties: { route_id: 'rt1', label: 'Route' } }] }} extra={{ routes: [route] }} />,
    );
    await user.click(screen.getByRole('button', { name: 'map click' }));
    const rd = await screen.findByTestId('route-details');
    expect(rd).toHaveTextContent('1.2 km');
    expect(rd).toHaveTextContent('Shorter than alternatives');
    expect(rd).toHaveTextContent('Elevation is approximate');
    unmount();
    render(
      <Harness
        click={{ lngLat: [0, 0], hits: [{ layerId: 'verified-symbol', properties: { layer: 'verified', name: 'Shelter', source_record_id: 'CBD-1' } }] }}
        extra={{ sources: [CBD] }}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'map click' }));
    const popup = await screen.findByTestId('source-popup');
    expect(popup).toHaveTextContent('City of Chicago');
    expect(screen.getAllByTestId('historical-label').length).toBeGreaterThan(0);
  });

  it('nearby list and coordinate form are keyboard reachable alternatives', async () => {
    const user = userEvent.setup();
    const fetchEdges = vi.fn(() => Promise.resolve([EDGE]));
    function Wrapper() {
      const inspector = useFeatureInspector({ fetchEdges });
      return (
        <>
          <button type="button" onClick={() => inspector.onMapClick({ lngLat: [0, 0], hits: [{ layerId: 'buildings-fill', properties: { name: 'B' } }] })}>go</button>
          <FeatureInspector inspector={inspector} />
        </>
      );
    }
    render(<Wrapper />);
    await user.click(screen.getByRole('button', { name: 'go' }));
    const nav = await screen.findByRole('navigation', { name: 'Nearby features' });
    await waitFor(() => expect(within(nav).getAllByRole('button')).toHaveLength(2));
    await user.click(within(nav).getByRole('button', { name: /Road segment/ }));
    expect(screen.getByTestId('inspect-road')).toBeInTheDocument();
    expect(within(nav).getByRole('button', { name: /Road segment/ })).toHaveAttribute('aria-current', 'true');
    await user.click(screen.getByRole('button', { name: 'Inspect location' }));
    expect(screen.getByRole('alert')).toHaveTextContent('latitude');
    await user.type(screen.getByLabelText('Latitude'), '41.88');
    await user.type(screen.getByLabelText('Longitude'), '-87.63');
    await user.click(screen.getByRole('button', { name: 'Inspect location' }));
    expect(fetchEdges).toHaveBeenLastCalledWith(41.88, -87.63, expect.anything());
  });
});
