import { useEffect, useRef, useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BottomSheet } from './BottomSheet';
import { SidePanel, useStoredPanelMode } from './SidePanel';
import { InspectorPanel } from './InspectorPanel';
import { PanelBody, PanelSlot } from './PanelBody';
import { stageLayout, type Detent, type PanelMode, type StageLayout } from './layout';
import { readPanelMode, writePanelMode } from './preferences';

/** Validates: Requirements 7.3, 7.4, 7.7, 7.11, 7.12, 7.14, 13.8, 14.8 */

afterEach(() => {
  vi.restoreAllMocks();
});

function Sheet({ initial = 'collapsed' }: { initial?: Detent }) {
  const [detent, setDetent] = useState<Detent>(initial);
  const mapRef = useRef<HTMLDivElement>(null);
  return (
    <>
      <div ref={mapRef} data-testid="map">
        <button type="button">Map control</button>
      </div>
      <BottomSheet detent={detent} onDetentChange={setDetent} available={700} mapRef={mapRef}>
        <button type="button">Inside sheet</button>
      </BottomSheet>
    </>
  );
}

const grabber = () => screen.getByRole('button', { name: /^Resize panel/ });

describe('BottomSheet grabber', () => {
  it.each(['{Enter}', ' '])('cycles Collapsed, Half, Full, Collapsed with %j', async (key) => {
    const user = userEvent.setup();
    render(<Sheet />);
    grabber().focus();
    const seen: (string | null)[] = [];
    for (let i = 0; i < 4; i++) {
      await user.keyboard(key);
      seen.push(screen.getByRole('region', { name: 'SafePath panel' }).getAttribute('data-detent'));
    }
    expect(seen).toEqual(['half', 'full', 'collapsed', 'half']);
  });

  it('steps with ArrowUp and ArrowDown and clamps at both ends', async () => {
    const user = userEvent.setup();
    render(<Sheet />);
    const region = screen.getByRole('region', { name: 'SafePath panel' });
    grabber().focus();
    await user.keyboard('{ArrowDown}');
    expect(region).toHaveAttribute('data-detent', 'collapsed');
    await user.keyboard('{ArrowUp}');
    expect(region).toHaveAttribute('data-detent', 'half');
    await user.keyboard('{ArrowUp}');
    expect(region).toHaveAttribute('data-detent', 'full');
    await user.keyboard('{ArrowUp}');
    expect(region).toHaveAttribute('data-detent', 'full');
    await user.keyboard('{ArrowDown}');
    expect(region).toHaveAttribute('data-detent', 'half');
  });

  it('reaches every Detent from the keyboard alone (none is drag-only)', async () => {
    const user = userEvent.setup();
    const reached = new Set<string>(['collapsed']);
    render(<Sheet />);
    const region = screen.getByRole('region', { name: 'SafePath panel' });
    grabber().focus();
    for (const key of ['{Enter}', '{Enter}', '{Enter}']) {
      await user.keyboard(key);
      reached.add(region.getAttribute('data-detent') ?? '');
    }
    expect([...reached].sort()).toEqual(['collapsed', 'full', 'half']);
  });

  it('exposes the grabber as a labelled button that names the current Detent', () => {
    render(<Sheet initial="half" />);
    expect(grabber()).toHaveAccessibleName('Resize panel, half');
  });

  it('returns focus to the grabber when leaving Full', () => {
    function Host() {
      const [detent, setDetent] = useState<Detent>('full');
      return (
        <>
          <button type="button" onClick={() => setDetent('half')}>
            Lower
          </button>
          <BottomSheet detent={detent} onDetentChange={setDetent} available={700} />
        </>
      );
    }
    render(<Host />);
    const lower = screen.getByRole('button', { name: 'Lower' });
    lower.focus();
    lower.click();
    return vi.waitFor(() => expect(grabber()).toHaveFocus());
  });

  it('makes the map inert and aria-hidden only while Full', async () => {
    const user = userEvent.setup();
    render(<Sheet initial="half" />);
    const map = screen.getByTestId('map');
    expect(map).not.toHaveAttribute('inert');
    grabber().focus();
    await user.keyboard('{Enter}');
    expect(map).toHaveAttribute('inert');
    expect(map).toHaveAttribute('aria-hidden', 'true');
    await user.keyboard('{Enter}');
    expect(map).not.toHaveAttribute('inert');
    expect(map).not.toHaveAttribute('aria-hidden');
  });
});

const layoutFor = (width: number, mode: PanelMode = 'default'): StageLayout => stageLayout(width, mode, false);

function Side({ layout }: { layout: StageLayout }) {
  const [mode, setMode] = useStoredPanelMode();
  return (
    <SidePanel mode={mode} onModeChange={setMode} layout={layout}>
      <input aria-label="Body input" />
    </SidePanel>
  );
}

describe('SidePanel', () => {
  beforeEach(() => {
    writePanelMode('default');
  });

  it('expands, restores, collapses, and brings the panel back from the rail', async () => {
    const user = userEvent.setup();
    const wide = layoutFor(1600);
    expect(wide.canExpand).toBe(true);
    render(<Side layout={wide} />);
    const region = screen.getByRole('region', { name: 'SafePath panel' });
    expect(region).toHaveAttribute('data-mode', 'default');

    await user.click(screen.getByRole('button', { name: 'Expand panel' }));
    expect(region).toHaveAttribute('data-mode', 'expanded');
    expect(readPanelMode()).toBe('expanded');

    await user.click(screen.getByRole('button', { name: 'Restore panel size' }));
    expect(region).toHaveAttribute('data-mode', 'default');

    await user.click(screen.getByRole('button', { name: 'Collapse panel' }));
    expect(region).toHaveAttribute('data-mode', 'collapsed');
    expect(readPanelMode()).toBe('collapsed');

    await user.click(screen.getByRole('button', { name: 'Restore panel' }));
    expect(region).toHaveAttribute('data-mode', 'default');
  });

  it('keeps body state mounted while collapsed', async () => {
    const user = userEvent.setup();
    render(<Side layout={layoutFor(1600)} />);
    await user.type(screen.getByLabelText('Body input'), 'hello');
    await user.click(screen.getByRole('button', { name: 'Collapse panel' }));
    await user.click(screen.getByRole('button', { name: 'Restore panel' }));
    expect(screen.getByLabelText('Body input')).toHaveValue('hello');
  });

  it.each<PanelMode>(['expanded', 'collapsed'])('restores the stored %s mode on a fresh mount', (stored) => {
    writePanelMode(stored);
    render(<Side layout={layoutFor(1600, stored)} />);
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toHaveAttribute('data-mode', stored);
  });

  it('falls back to default for an unrecognised stored value', () => {
    window.localStorage.setItem('safepath.panelMode', 'sideways');
    render(<Side layout={layoutFor(1600)} />);
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toHaveAttribute('data-mode', 'default');
    window.localStorage.removeItem('safepath.panelMode');
  });

  it('disables Expand and explains why when canExpand is false', () => {
    const tight = layoutFor(900);
    expect(tight.canExpand).toBe(false);
    render(<Side layout={tight} />);
    const expand = screen.getByRole('button', { name: 'Expand panel' });
    expect(expand).toBeDisabled();
    expect(expand).toHaveAccessibleDescription(/not enough room/i);
  });

  it('keeps Collapse and the body usable when Expand is disabled', async () => {
    const user = userEvent.setup();
    render(<Side layout={layoutFor(900)} />);
    await user.click(screen.getByRole('button', { name: 'Expand panel' }));
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toHaveAttribute('data-mode', 'default');
    await user.click(screen.getByRole('button', { name: 'Collapse panel' }));
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toHaveAttribute('data-mode', 'collapsed');
  });
});

function MovingHost({ onReady }: { onReady: (move: () => void) => void }) {
  const [frame, setFrame] = useState<'a' | 'b'>('a');
  useEffect(() => {
    onReady(() => setFrame((f) => (f === 'a' ? 'b' : 'a')));
  }, [onReady]);
  return (
    <PanelBody
      frame={
        frame === 'a' ? (
          <section data-testid="frame-a">
            <PanelSlot title="Panel A" />
          </section>
        ) : (
          <aside data-testid="frame-b">
            <PanelSlot title="Panel B" />
          </aside>
        )
      }
    >
      <input id="move-input" aria-label="Moving input" />
    </PanelBody>
  );
}

describe('PanelBody', () => {
  it('keeps typed input and focus when the host moves', async () => {
    const user = userEvent.setup();
    let move: () => void = () => {};
    render(<MovingHost onReady={(m) => (move = m)} />);
    const input = screen.getByLabelText('Moving input');
    expect(screen.getByTestId('frame-a')).toContainElement(input);

    await user.type(input, 'hello');
    expect(input).toHaveFocus();

    act(() => move());

    expect(screen.queryByTestId('frame-a')).not.toBeInTheDocument();
    const moved = screen.getByLabelText('Moving input');
    expect(screen.getByTestId('frame-b')).toContainElement(moved);
    expect(moved).toBe(input);
    expect(moved).toHaveValue('hello');
    expect(moved).toHaveFocus();
  });

  it('falls back to the group named by the panel title when the focused control is gone', async () => {
    const user = userEvent.setup();
    let move: () => void = () => {};
    function Host() {
      const [frame, setFrame] = useState<'a' | 'b'>('a');
      useEffect(() => {
        move = () => setFrame((f) => (f === 'a' ? 'b' : 'a'));
      }, []);
      return (
        <PanelBody
          frame={
            frame === 'a' ? (
              <section data-testid="frame-a">
                <PanelSlot title="Panel A" />
              </section>
            ) : (
              <aside data-testid="frame-b">
                <PanelSlot title="Panel B" />
              </aside>
            )
          }
        >
          {frame === 'a' ? <input id="field" aria-label="Field" /> : <p>No field here</p>}
        </PanelBody>
      );
    }
    render(<Host />);
    await user.click(screen.getByLabelText('Field'));
    expect(screen.getByLabelText('Field')).toHaveFocus();

    act(() => move());

    expect(screen.queryByLabelText('Field')).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Panel B' })).toHaveFocus();
  });
});

function Inspector({ onCloseSpy }: { onCloseSpy?: () => void }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <div data-focus-target="map" role="region" aria-label="Map" />
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <InspectorPanel
        open={open}
        title="Lamp post"
        onClose={() => {
          onCloseSpy?.();
          setOpen(false);
        }}
      >
        <p>Details</p>
      </InspectorPanel>
    </>
  );
}

describe('InspectorPanel', () => {
  it('shows the title and a labelled close control', () => {
    render(<Inspector />);
    expect(screen.getByRole('heading', { name: 'Lamp post' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close map feature details' })).toBeInTheDocument();
  });

  it('closes with the close control and returns focus to the map region', async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Inspector onCloseSpy={spy} />);
    await user.click(screen.getByRole('button', { name: 'Close map feature details' }));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Map' })).toHaveFocus();
  });

  it('closes on Escape and returns focus to the map region', async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Inspector onCloseSpy={spy} />);
    screen.getByRole('button', { name: 'Close map feature details' }).focus();
    await user.keyboard('{Escape}');
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Map' })).toHaveFocus();
  });

  it('renders nothing and ignores Escape while closed', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<InspectorPanel open={false} title="x" onClose={onClose} />);
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });
});
