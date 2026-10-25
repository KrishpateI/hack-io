import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { RouteTab, type RouteTabProps } from './RouteTab';

const base = (over: Partial<RouteTabProps> = {}): RouteTabProps => ({
  start: null,
  destination: null,
  pickMode: null,
  onPickModeChange: vi.fn(),
  onStartChange: vi.fn(),
  onDestinationChange: vi.fn(),
  travelMode: 'walking',
  onTravelModeChange: vi.fn(),
  stepFree: false,
  onStepFreeChange: vi.fn(),
  routePanel: { response: null },
  onOpenRouteDetails: vi.fn(),
  ...over,
});

describe('RouteTab', () => {
  it('renders setup controls and keeps coordinate ids', async () => {
    const onMode = vi.fn();
    const onStep = vi.fn();
    const onDetails = vi.fn();
    const user = userEvent.setup();
    const { container } = render(<RouteTab {...base({ onTravelModeChange: onMode, onStepFreeChange: onStep, onOpenRouteDetails: onDetails })} />);
    expect(container.querySelector('#start-lat')).not.toBeNull();
    expect(container.querySelector('#dest-lng')).not.toBeNull();
    expect(screen.getAllByText('Not set')).toHaveLength(2);
    await user.click(screen.getByRole('radio', { name: 'Wheelchair' }));
    expect(onMode).toHaveBeenCalledWith('wheelchair');
    await user.click(screen.getByRole('switch', { name: 'Step-free route' }));
    expect(onStep).toHaveBeenCalledWith(true);
    await user.click(screen.getByRole('button', { name: 'Route details' }));
    expect(onDetails).toHaveBeenCalled();
  });

  it('hides mode and step-free for indoor and shows shelter-note when sheltering', () => {
    render(<RouteTab {...base({ indoor: true, shelter: true })} />);
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByTestId('shelter-note')).toHaveTextContent('no route is shown');
  });
});
