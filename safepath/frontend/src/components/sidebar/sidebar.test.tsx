import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { Incident, RouteResponse } from '../../services/api';
import { LeftSidebar } from './LeftSidebar';
import { validateCoordinates, SIM_DESTINATION_LABEL, type Destination, type LatLng } from './StartDestinationPicker';
import { RouteUncertaintySummary } from './RouteUncertaintySummary';
import { OFFICIAL_GUIDANCE } from './OfficialGuidance';

const inc = (id: string, type: Incident['incident_type'], sim: boolean): Incident => ({
  incident_id: id, incident_type: type, simulation_flag: sim, status: 'active', source: 's',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
});
const incidents = [inc('flood-1', 'flood', true), inc('fire-1', 'fire', false)];

function Harness({ onStart, onDest }: { onStart?: (p: LatLng) => void; onDest?: (p: Destination) => void }) {
  const [type, setType] = useState<Incident['incident_type'] | null>(null);
  const [sel, setSel] = useState<Incident | null>(null);
  const [start, setStart] = useState<LatLng | null>(null);
  const [dest, setDest] = useState<Destination | null>(null);
  const [pick, setPick] = useState<'start' | 'destination' | null>(null);
  return (
    <LeftSidebar incident={sel} incidents={incidents} disasterType={type} start={start} destination={dest} pickMode={pick}
      routeResponse={null} onSelectType={setType} onSelectIncident={setSel}
      onStartChange={(p) => { setStart(p); onStart?.(p); }} onDestinationChange={(p) => { setDest(p); onDest?.(p); }} onPickModeChange={setPick} />
  );
}

describe('LeftSidebar', () => {
  it('lists incidents of the selected type with SIMULATED scenario label and routing description', async () => {
    const u = userEvent.setup();
    render(<Harness />);
    await u.click(screen.getByRole('button', { name: /^Tornado/ }));
    expect(screen.getByTestId('disaster-description')).toHaveTextContent(/shelter guidance rather than an outdoor route/i);
    await u.click(screen.getByRole('button', { name: /^Flood/ }));
    await u.click(screen.getByRole('button', { name: /flood-1/ }));
    expect(screen.getAllByText('SIMULATED scenario').length).toBeGreaterThan(0);
    expect(screen.queryByText(/fire-1/)).toBeNull();
    expect(screen.getByTestId('active-incident')).toHaveTextContent('SIMULATED');
  });

  it('sample points fill start and a simulation destination with the not-a-shelter label', async () => {
    const u = userEvent.setup();
    const onDest = vi.fn();
    render(<Harness onDest={onDest} />);
    expect(screen.queryByText(/Use sample points/)).toBeNull();
    await u.click(screen.getByRole('button', { name: /^Flood/ }));
    await u.click(screen.getByRole('button', { name: /Use sample points/ }));
    expect(onDest).toHaveBeenCalledWith(expect.objectContaining({ simulated: true }));
    expect(screen.getByTestId('sim-destination-label')).toHaveTextContent(SIM_DESTINATION_LABEL);
  });

  it('rejects out-of-bounds and non-numeric coordinates with a clear error', async () => {
    const u = userEvent.setup();
    const onStart = vi.fn();
    render(<Harness onStart={onStart} />);
    await u.type(screen.getByLabelText('Latitude', { selector: '#start-lat' }), '10');
    await u.type(screen.getByLabelText('Longitude', { selector: '#start-lng' }), '20');
    await u.tab();
    expect(screen.getByRole('alert')).toHaveTextContent(/Outside the Chicago extraction area/);
    expect(onStart).not.toHaveBeenCalled();
    expect(validateCoordinates('abc', '1')).toMatch(/numeric/);
    expect(validateCoordinates('41.88', '-87.63')).toBeNull();
  });

  it('shows official links, static guidance disclaimer and source/date', () => {
    render(<Harness />);
    expect(screen.getByRole('link', { name: /Chicago OEMC/ })).toBeInTheDocument();
    expect(screen.getByTestId('official-guidance')).toHaveTextContent('Static guidance, not a live order');
    expect(screen.getByTestId('official-guidance')).toHaveTextContent(/Retrieved \d{4}-\d{2}-\d{2}/);
    expect(OFFICIAL_GUIDANCE.guidance.length).toBeGreaterThan(0);
  });

  it('collapses on small screens via the toggle', async () => {
    const u = userEvent.setup();
    render(<Harness />);
    const t = screen.getByRole('button', { name: /Hide incident and route setup/ });
    await u.click(t);
    expect(t).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('RouteUncertaintySummary', () => {
  it('shows level, limitations, label and historical label for CBD notices', () => {
    const response = {
      policy_outcome: 'ROUTE_OPTIONS_AVAILABLE', explanation: '', calculated_at: 'x', label: 'algorithm-generated',
      notices: [{ cbd_derived: true }],
      routes: [{ uncertainty_level: 'High', label: 'algorithm-generated', confidence_limitations: ['Elevation data missing'] }],
    } as unknown as RouteResponse;
    render(<RouteUncertaintySummary response={response} />);
    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getByText('Elevation data missing')).toBeInTheDocument();
    expect(screen.getByText('algorithm-generated')).toBeInTheDocument();
    expect(screen.getByTestId('historical-label')).toBeInTheDocument();
  });
});
