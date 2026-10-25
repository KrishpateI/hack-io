import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MapLayerControls } from './MapLayerControls';
import { DEFAULT_LAYER_VISIBILITY, INITIAL_TOGGLES, useLayerToggles } from './useLayerToggles';
import { TOGGLES } from './layers/styles';

describe('MapLayerControls', () => {
  it('renders a named switch with On/Off text for every layer', () => {
    render(<MapLayerControls layers={INITIAL_TOGGLES} onToggle={() => {}} />);
    const group = screen.getByRole('region', { name: 'Map layers' });
    for (const t of INITIAL_TOGGLES) {
      const sw = within(group).getByRole('switch', { name: t.label });
      expect(sw).toHaveProperty('checked', t.visible);
    }
    expect(within(group).getByRole('switch', { name: 'Data extraction boundary' })).toBeChecked();
    // The street graph and low-lying toggles were removed (no data source / merged into flood area).
    expect(within(group).queryByRole('switch', { name: 'Street graph (faint)' })).toBeNull();
    expect(within(group).queryByRole('switch', { name: 'SIMULATED low-lying areas' })).toBeNull();
  });

  it('calls onToggle with the layer id and the new state', async () => {
    const onToggle = vi.fn();
    render(<MapLayerControls layers={INITIAL_TOGGLES} onToggle={onToggle} />);
    await userEvent.click(screen.getByRole('switch', { name: 'Data extraction boundary' }));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledWith('extraction', false);
    await userEvent.click(screen.getByRole('switch', { name: 'Buildings (height class)' }));
    expect(onToggle).toHaveBeenLastCalledWith('buildings', false);
  });

  it('passes legend presence through to the legend', () => {
    const all = INITIAL_TOGGLES.map((t) => ({ ...t, visible: true }));
    render(<MapLayerControls layers={all} onToggle={() => {}} legendPresence={{ start: true }} />);
    expect(screen.getByTestId('legend-start')).toBeInTheDocument();
    expect(screen.queryByText(/Height Unknown/)).toBeNull();
  });

  it('reflects the visibility it is given', () => {
    const layers = INITIAL_TOGGLES.map((t) => ({ ...t, visible: t.id !== 'extraction' }));
    render(<MapLayerControls layers={layers} onToggle={() => {}} />);
    const sw = screen.getByRole('switch', { name: 'Data extraction boundary' });
    expect(sw).not.toBeChecked();
    expect(sw.closest('div.flex')).toHaveTextContent('Off');
  });

  it('renders the legend and omits entries of hidden layers', () => {
    const all = INITIAL_TOGGLES.map((t) => ({ ...t, visible: true }));
    const { rerender } = render(<MapLayerControls layers={all} onToggle={() => {}} />);
    expect(screen.getByTestId('map-legend')).toBeInTheDocument();
    expect(screen.getByText(/Height Unknown/)).toBeInTheDocument();
    rerender(
      <MapLayerControls
        layers={all.map((t) => (t.id === 'buildings' ? { ...t, visible: false } : t))}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByTestId('map-legend')).toBeInTheDocument();
    expect(screen.queryByText(/Height Unknown/)).toBeNull();
  });

  it('shows the historical note in the legend when historical layers are given', () => {
    render(<MapLayerControls layers={INITIAL_TOGGLES} onToggle={() => {}} historicalLayers={['flood']} />);
    expect(screen.getByTestId('legend-historical')).toBeInTheDocument();
  });
});

describe('useLayerToggles', () => {
  function Harness() {
    const { layerVisibility, controls } = useLayerToggles();
    return (
      <div>
        <output data-testid="vis">{JSON.stringify(layerVisibility)}</output>
        {controls}
      </div>
    );
  }
  const visibility = () => JSON.parse(screen.getByTestId('vis').textContent ?? '{}') as Record<string, boolean>;

  it('starts from the defaults: extraction plus every layer toggle', () => {
    render(<Harness />);
    expect(visibility()).toEqual(DEFAULT_LAYER_VISIBILITY);
    expect(INITIAL_TOGGLES.map((t) => t.id)).toEqual(['extraction', ...TOGGLES.map((t) => t.id)]);
    expect('graph' in DEFAULT_LAYER_VISIBILITY).toBe(false);
    expect('elevation' in DEFAULT_LAYER_VISIBILITY).toBe(false);
    expect(DEFAULT_LAYER_VISIBILITY.extraction).toBe(true);
  });

  it('switching the Data extraction boundary updates layerVisibility and the switch state', async () => {
    render(<Harness />);
    const sw = screen.getByRole('switch', { name: 'Data extraction boundary' });
    await userEvent.click(sw);
    expect(visibility().extraction).toBe(false);
    expect(screen.getByRole('switch', { name: 'Data extraction boundary' })).not.toBeChecked();
    await userEvent.click(screen.getByRole('switch', { name: 'Buildings (height class)' }));
    expect(visibility().buildings).toBe(false);
    expect(visibility().extraction).toBe(false);
  });
});
