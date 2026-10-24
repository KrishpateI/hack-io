import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { demoBuilding, ROUTE_EDGES } from './fixture';
import { IndoorBuildingView, SIMULATED_BANNER } from './IndoorBuildingView';

const report = (over: Record<string, unknown> = {}) => ({
  report_id: 'r1',
  indoor_feature_id: 'stair-A',
  category: 'inaccessible stairwell',
  verification_status: 'Unverified',
  created_at: '2025-01-01T00:00:00Z',
  ...over,
});

describe('IndoorBuildingView', () => {
  it('shows the SIMULATED banner, floors, stairwells and exits with labels', () => {
    render(<IndoorBuildingView building={demoBuilding()} />);
    expect(screen.getByTestId('indoor-banner')).toHaveTextContent(SIMULATED_BANNER);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['L1', 'L2', 'L3']);
    expect(screen.getByRole('button', { name: /SIMULATED Stairwell A, No reports/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SIMULATED Exit 1, No reports/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SIMULATED Stairwell C/ })).toBeInTheDocument();
  });

  it('switches floors with the keyboard; exits only on the ground floor', async () => {
    const user = userEvent.setup();
    render(<IndoorBuildingView building={demoBuilding()} />);
    screen.getByRole('tab', { name: /Floor 1/ }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: /Floor 2/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Floor 2/ })).toHaveFocus();
    expect(screen.queryByTestId('indoor-feature-exit-1')).toBeNull();
    await user.keyboard('{Home}');
    expect(screen.getByTestId('indoor-feature-exit-1')).toBeInTheDocument();
  });

  it('opens the panel on keyboard activation and fires callbacks', async () => {
    const user = userEvent.setup();
    const onReport = vi.fn();
    const onCorroborate = vi.fn();
    const onDispute = vi.fn();
    render(
      <IndoorBuildingView building={demoBuilding()} reports={[report()]} onReport={onReport} onCorroborate={onCorroborate} onDispute={onDispute} />,
    );
    screen.getByTestId('indoor-feature-stair-A').focus();
    await user.keyboard('{Enter}');
    const panel = screen.getByTestId('indoor-panel');
    expect(within(panel).getByText('Reported blocked \u2014 Unverified')).toBeInTheDocument();
    expect(screen.getByTestId('indoor-panel-count')).toHaveTextContent('Reports: 1');
    await user.click(within(panel).getByRole('button', { name: 'Report this stairwell blocked' }));
    expect(onReport).toHaveBeenCalledWith('stair-A');
    await user.click(within(panel).getByRole('button', { name: 'Corroborate latest report' }));
    await user.click(within(panel).getByRole('button', { name: 'Dispute latest report' }));
    expect(onCorroborate).toHaveBeenCalledWith('r1');
    expect(onDispute).toHaveBeenCalledWith('r1');
  });

  it('updates live and announces status changes without reload', () => {
    const b = demoBuilding();
    const { rerender } = render(<IndoorBuildingView building={b} reports={[]} />);
    expect(screen.getByTestId('indoor-live')).toHaveTextContent('');
    rerender(<IndoorBuildingView building={b} reports={[report({ verification_status: 'Corroborated' })]} />);
    expect(screen.getByRole('button', { name: /Stairwell A, Reported blocked \u2014 Corroborated by community, not officially verified/ })).toBeInTheDocument();
    expect(screen.getByTestId('indoor-live')).toHaveTextContent('Stairwell A: Reported blocked \u2014 Corroborated by community, not officially verified');
    rerender(<IndoorBuildingView building={b} restrictions={[{ kind: 'stairwell_closure', edge_ids: ['sim-indoor-t-stair-A-L1-L2'] }]} />);
    expect(screen.getByTestId('indoor-live')).toHaveTextContent('Closure confirmed (SIMULATED moderator verification)');
  });

  it('draws the route, lists steps and names the removed stairwell', async () => {
    const user = userEvent.setup();
    render(
      <IndoorBuildingView
        building={demoBuilding()}
        initialLevel="L3"
        edgeIds={ROUTE_EDGES}
        restrictions={[{ kind: 'stairwell_closure', edge_ids: ['sim-indoor-t-stair-A-L1-L2'] }]}
      />,
    );
    expect(screen.getAllByTestId('indoor-route-leg').length).toBeGreaterThan(0);
    const route = screen.getByTestId('indoor-route');
    expect(within(route).getByText('Stairwell B down to Floor 2')).toBeInTheDocument();
    expect(within(route).getByText('Exit 1')).toBeInTheDocument();
    expect(within(route).getByText('Stairwell A removed from the route: confirmed closure (SIMULATED)')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /Floor 1/ }));
    expect(screen.getAllByTestId('indoor-route-leg').length).toBeGreaterThan(0);
  });

  it('shows emergency guidance when no route exists', () => {
    render(<IndoorBuildingView building={demoBuilding()} route={{ policy_outcome: 'NO_VERIFIED_ROUTE_AVAILABLE', routes: [] }} />);
    expect(screen.getByTestId('indoor-no-route')).toHaveTextContent(/building staff/);
    expect(screen.getByTestId('indoor-no-route')).toHaveTextContent(/911/);
  });

  it('shows an error state with retry when building data is unavailable', async () => {
    const user = userEvent.setup();
    const load = vi.fn().mockRejectedValueOnce(new Error('HTTP 503')).mockResolvedValueOnce(demoBuilding());
    render(<IndoorBuildingView loadBuilding={load} />);
    expect(await screen.findByTestId('indoor-unavailable')).toHaveTextContent('unavailable');
    expect(screen.getByTestId('indoor-banner')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('tab', { name: /Floor 1/ })).toBeInTheDocument();
  });

  it('never claims a stairwell is safe', () => {
    render(<IndoorBuildingView building={demoBuilding()} />);
    expect(screen.getByTestId('indoor-view').textContent).not.toMatch(/\bsafe\b/i);
  });
});
