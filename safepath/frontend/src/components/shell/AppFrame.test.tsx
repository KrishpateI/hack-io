import { useState, type ReactNode } from 'react';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DisclaimerBanner } from '../DisclaimerBanner';
import { AppFrame, type AppFrameProps } from './AppFrame';
import { TAB_IDS, type TabId } from './navigation';

/** Validates: Requirements 3.2, 3.5, 6.1, 6.2, 7.1, 7.5, 7.6, 7.9, 7.15, 13.8, 14.8 */

// MapLibre needs WebGL; the frame only needs to know what padding it passes down.
const h = vi.hoisted(() => ({
  mapProps: null as null | { viewPadding?: { top?: number; right?: number; bottom?: number; left?: number } },
}));
vi.mock('../../maps/MapView', () => ({
  MapView: (props: NonNullable<typeof h.mapProps>) => {
    h.mapProps = props;
    return <div data-testid="map-stub" />;
  },
}));

const ORIGINAL_WIDTH = window.innerWidth;

function setViewport(width: number) {
  act(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
    window.dispatchEvent(new Event('resize'));
  });
}

const startAt = (hash: string) => window.history.replaceState(null, '', hash);

beforeEach(() => {
  window.localStorage.clear();
  startAt('#/tab/map');
  h.mapProps = null;
});

afterEach(() => {
  setViewport(ORIGINAL_WIDTH);
  startAt('#/tab/map');
});

const tabContent = (id: TabId): ReactNode => (
  <div data-testid={`content-${id}`}>
    {id === 'route' ? <input id="start-lat" aria-label="Start latitude" /> : null}
  </div>
);

const defaultTabs = Object.fromEntries(TAB_IDS.map((id) => [id, () => tabContent(id)])) as Record<
  TabId,
  () => ReactNode
>;

const defaultScreen: AppFrameProps['renderScreen'] = (ctx) =>
  ctx.screen || ctx.demo ? (
    <div role="dialog" aria-label={ctx.screen ?? 'demo'}>
      <button type="button" onClick={ctx.onClose}>
        Close screen
      </button>
      <button type="button" onClick={ctx.onBack}>
        {`Back to ${ctx.backLabel}`}
      </button>
    </div>
  ) : null;

/** The frame with the header and Disclaimer_Banner rendered above it, as AppShell does. */
function Page({ props }: { props: Partial<AppFrameProps> }) {
  return (
    <div>
      <DisclaimerBanner />
      <header data-testid="app-header">SafePath</header>
      <AppFrame renderTab={defaultTabs} renderScreen={defaultScreen} {...props} />
    </div>
  );
}

function renderFrame(initial: Partial<AppFrameProps> = {}) {
  let current = initial;
  const utils = render(<Page props={current} />);
  return {
    ...utils,
    update(next: Partial<AppFrameProps>) {
      current = { ...current, ...next };
      utils.rerender(<Page props={current} />);
    },
  };
}

const stage = () => screen.getByTestId('dashboard');
const panel = () => screen.getByRole('region', { name: 'SafePath panel' });
const grabber = () => screen.getByRole('button', { name: /^Resize panel/ });
const tabButton = (label: string) =>
  within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', {
    name: new RegExp(`^${label}`),
  });

describe('AppFrame stage', () => {
  it.each([
    [320, 'compact'],
    [1024, 'wide'],
    [1280, 'desktop'],
    [2560, 'desktop'],
  ])('marks the stage with data-viewport-tier at %i px', (width, tier) => {
    setViewport(width);
    renderFrame();
    expect(stage()).toHaveAttribute('data-viewport-tier', tier);
  });

  it('follows a resize without remounting the stage', () => {
    setViewport(1024);
    renderFrame();
    const el = stage();
    setViewport(320);
    expect(stage()).toBe(el);
    expect(stage()).toHaveAttribute('data-viewport-tier', 'compact');
  });
});

describe('AppFrame right frame per tier', () => {
  it('compact: Bottom_Sheet plus Tab_Bar, no Side_Panel, no Inspector_Panel', () => {
    setViewport(320);
    renderFrame({ inspector: { open: true, title: 'Lamp', onClose: vi.fn(), content: <p>x</p> } });
    expect(panel()).toHaveAttribute('data-detent');
    expect(panel()).not.toHaveAttribute('data-mode');
    expect(grabber()).toBeInTheDocument();
    expect(screen.getByTestId('compact-bottom')).toContainElement(
      screen.getByRole('navigation', { name: 'Primary' }),
    );
    expect(screen.getByTestId('tab-selected-indicator')).toBeInTheDocument();
    expect(screen.queryByTestId('wide-frame')).not.toBeInTheDocument();
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^(Map|Route|Community|Safety|More)/ })).toHaveLength(5);
  });

  it('wide: Side_Panel with the Tab_List, no Bottom_Sheet, no Inspector_Panel', () => {
    setViewport(1024);
    renderFrame({ inspector: { open: true, title: 'Lamp', onClose: vi.fn(), content: <p>x</p> } });
    expect(panel()).toHaveAttribute('data-mode', 'default');
    expect(screen.getByTestId('wide-frame')).toContainElement(
      screen.getByRole('navigation', { name: 'Primary' }),
    );
    expect(screen.getByTestId('tab-selected-bar')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Resize panel/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId('compact-bottom')).not.toBeInTheDocument();
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
  });

  it.each([1280, 2560])('desktop at %i px: Side_Panel plus Inspector_Panel while a feature is selected', (width) => {
    setViewport(width);
    const { update } = renderFrame();
    expect(panel()).toHaveAttribute('data-mode', 'default');
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
    update({ inspector: { open: true, title: 'Lamp post', onClose: vi.fn(), content: <p>Details</p> } });
    const inspector = screen.getByTestId('inspector-panel');
    expect(within(inspector).getByRole('heading', { name: 'Lamp post' })).toBeInTheDocument();
    expect(within(inspector).getByText('Details')).toBeInTheDocument();
    expect(inspector).not.toContainElement(panel());
    expect(screen.queryByTestId('compact-bottom')).not.toBeInTheDocument();
  });

  it('renders only the active tab content', () => {
    setViewport(1024);
    renderFrame();
    expect(screen.getByTestId('content-map')).toBeInTheDocument();
    expect(screen.queryByTestId('content-route')).not.toBeInTheDocument();
  });

  it('passes panel padding to the map: left at wide, bottom at compact', () => {
    setViewport(1024);
    renderFrame();
    expect(h.mapProps?.viewPadding?.left).toBeGreaterThan(0);
    expect(h.mapProps?.viewPadding?.right).toBe(0);
    setViewport(320);
    expect(h.mapProps?.viewPadding?.bottom).toBeGreaterThan(0);
    expect(h.mapProps?.viewPadding?.left).toBeUndefined();
  });
});

function Selectable({ initialTab }: { initialTab?: TabId }) {
  const [open, setOpen] = useState(false);
  const select = (
    <button type="button" onClick={() => setOpen(true)}>
      Select feature
    </button>
  );
  return (
    <AppFrame
      renderTab={{ ...defaultTabs, map: () => select, route: () => (initialTab === 'route' ? select : null) }}
      renderScreen={defaultScreen}
      inspector={{ open, title: 'Lamp post', onClose: () => setOpen(false), content: <p>Details</p> }}
    />
  );
}

describe('AppFrame state across tier changes', () => {
  it('keeps the tab, the Detent, and typed values when the tier changes', async () => {
    const user = userEvent.setup();
    setViewport(320);
    renderFrame();
    await user.click(tabButton('Route'));
    const input = screen.getByLabelText('Start latitude');
    await user.type(input, '41.88');
    grabber().focus();
    await user.keyboard('{Enter}');
    expect(panel()).toHaveAttribute('data-detent', 'full');

    setViewport(1280);
    expect(stage()).toHaveAttribute('data-viewport-tier', 'desktop');
    expect(screen.getByLabelText('Start latitude')).toBe(input);
    expect(input).toHaveValue('41.88');
    expect(tabButton('Route')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('panel-slot')).toContainElement(input);

    setViewport(320);
    expect(screen.getByLabelText('Start latitude')).toBe(input);
    expect(input).toHaveValue('41.88');
    expect(tabButton('Route')).toHaveAttribute('aria-current', 'page');
    expect(panel()).toHaveAttribute('data-detent', 'full');
  });

  it('keeps the selection: the Inspector_Panel returns when the viewport grows back to desktop', async () => {
    const user = userEvent.setup();
    setViewport(1280);
    render(<Selectable />);
    await user.click(screen.getByRole('button', { name: 'Select feature' }));
    expect(screen.getByTestId('inspector-panel')).toBeInTheDocument();
    expect(h.mapProps?.viewPadding?.right).toBeGreaterThan(0);

    setViewport(1024);
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
    expect(h.mapProps?.viewPadding?.right).toBe(0);

    setViewport(1280);
    expect(screen.getByTestId('inspector-panel')).toBeInTheDocument();
  });

  it('shows the inspector in the Map tab below desktop by bringing that tab forward', async () => {
    const user = userEvent.setup();
    startAt('#/tab/route');
    setViewport(1024);
    render(<Selectable initialTab="route" />);
    expect(tabButton('Route')).toHaveAttribute('aria-current', 'page');
    await user.click(screen.getByRole('button', { name: 'Select feature' }));
    expect(tabButton('Map')).toHaveAttribute('aria-current', 'page');
    expect(window.location.hash).toBe('#/tab/map');
    expect(screen.queryByTestId('inspector-panel')).not.toBeInTheDocument();
  });

  it('keeps the current tab at desktop when a feature is selected (the panel is separate)', async () => {
    const user = userEvent.setup();
    startAt('#/tab/route');
    setViewport(1280);
    render(<Selectable initialTab="route" />);
    await user.click(screen.getByRole('button', { name: 'Select feature' }));
    expect(tabButton('Route')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('inspector-panel')).toBeInTheDocument();
  });
});

describe('AppFrame pick mode and Detent', () => {
  it('starts collapsed on compact and raises to Half when a tab is chosen', async () => {
    const user = userEvent.setup();
    setViewport(320);
    renderFrame();
    expect(panel()).toHaveAttribute('data-detent', 'collapsed');
    await user.click(tabButton('Community'));
    expect(panel()).toHaveAttribute('data-detent', 'half');
  });

  it('lowers the sheet to Collapsed and shows the pick hint in pick mode (compact)', async () => {
    const user = userEvent.setup();
    setViewport(320);
    const { update } = renderFrame();
    await user.click(tabButton('Route'));
    expect(panel()).toHaveAttribute('data-detent', 'half');
    expect(screen.queryByTestId('pick-hint')).not.toBeInTheDocument();

    update({ pickMode: 'start' });
    expect(panel()).toHaveAttribute('data-detent', 'collapsed');
    const hint = screen.getByTestId('pick-hint');
    expect(hint).toHaveTextContent(/Click the map to set the start/);
    expect(panel()).not.toContainElement(hint);

    update({ pickMode: null });
    expect(screen.queryByTestId('pick-hint')).not.toBeInTheDocument();
  });

  it('shows the pick hint outside the Side_Panel on wide and calls onCancelPick', async () => {
    const user = userEvent.setup();
    const onCancelPick = vi.fn();
    setViewport(1024);
    renderFrame({ pickMode: 'destination', onCancelPick });
    const hint = screen.getByTestId('pick-hint');
    expect(hint).toHaveTextContent(/set the destination/);
    expect(panel()).not.toContainElement(hint);
    await user.click(within(hint).getByRole('button', { name: 'Cancel' }));
    expect(onCancelPick).toHaveBeenCalledTimes(1);
  });

  it('raises a collapsed sheet to Half when a route is computed', () => {
    setViewport(320);
    const { update } = renderFrame();
    expect(panel()).toHaveAttribute('data-detent', 'collapsed');
    update({ routeReady: true });
    expect(panel()).toHaveAttribute('data-detent', 'half');
  });

  it('does not raise the sheet for a route that arrives during pick mode', () => {
    setViewport(320);
    const { update } = renderFrame({ pickMode: 'start' });
    update({ routeReady: true });
    expect(panel()).toHaveAttribute('data-detent', 'collapsed');
  });

  it('raises a collapsed sheet to Half when a feature is selected', async () => {
    const user = userEvent.setup();
    setViewport(320);
    render(<Selectable />);
    expect(panel()).toHaveAttribute('data-detent', 'collapsed');
    await user.click(screen.getByRole('button', { name: 'Select feature' }));
    expect(panel()).toHaveAttribute('data-detent', 'half');
  });

  it('leaves a Full sheet at Full when a route is computed', async () => {
    const user = userEvent.setup();
    setViewport(320);
    const { update } = renderFrame();
    grabber().focus();
    await user.keyboard('{Enter}{Enter}');
    expect(panel()).toHaveAttribute('data-detent', 'full');
    update({ routeReady: true });
    expect(panel()).toHaveAttribute('data-detent', 'full');
  });
});

describe('AppFrame "Updated" badge', () => {
  it.each([320, 1024])('shows text on the Route tab at %i px while another tab is active', (width) => {
    setViewport(width);
    renderFrame({ routeChanged: true });
    expect(tabButton('Route')).toHaveTextContent('Updated');
    expect(screen.getByTestId('tab-badge-route')).toHaveTextContent('Updated');
    expect(screen.getByTestId('frame-announcer')).toHaveTextContent(/Route updated/);
  });

  it('is absent without a change, and clears once the Route tab is active', async () => {
    const user = userEvent.setup();
    setViewport(320);
    const { update } = renderFrame();
    expect(screen.queryByTestId('tab-badge-route')).not.toBeInTheDocument();
    update({ routeChanged: true });
    expect(screen.getByTestId('tab-badge-route')).toBeInTheDocument();
    await user.click(tabButton('Route'));
    expect(screen.queryByTestId('tab-badge-route')).not.toBeInTheDocument();
    expect(screen.getByTestId('frame-announcer')).toBeEmptyDOMElement();
  });
});

describe('AppFrame pushed screens', () => {
  it('renders the screen over its parent tab, highlights that tab, and hides the tab content', () => {
    startAt('#/route-details');
    setViewport(1024);
    renderFrame();
    expect(screen.getByRole('dialog', { name: 'route-details' })).toBeInTheDocument();
    expect(tabButton('Route')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('content-route').closest('[hidden]')).not.toBeNull();
  });

  it('closing returns to the parent tab with its content visible', async () => {
    const user = userEvent.setup();
    startAt('#/route-details');
    setViewport(1024);
    renderFrame();
    await user.click(screen.getByRole('button', { name: 'Close screen' }));
    expect(window.location.hash).toBe('#/tab/route');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('content-route').closest('[hidden]')).toBeNull();
    expect(tabButton('Route')).toHaveAttribute('aria-current', 'page');
  });

  it('back goes to the parent tab after a deep link, labelled with its title', async () => {
    const user = userEvent.setup();
    startAt('#/submit-report');
    setViewport(320);
    renderFrame();
    expect(tabButton('Safety')).toHaveAttribute('aria-current', 'page');
    await user.click(screen.getByRole('button', { name: 'Back to Safety' }));
    expect(window.location.hash).toBe('#/tab/safety');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('content-safety')).toBeInTheDocument();
  });

  it('treats the demo console as a screen under the More tab', () => {
    startAt('#/demo');
    setViewport(1024);
    renderFrame();
    expect(screen.getByRole('dialog', { name: 'demo' })).toBeInTheDocument();
    expect(tabButton('More')).toHaveAttribute('aria-current', 'page');
  });

  it('raises a collapsed sheet to Half when a screen opens', () => {
    setViewport(320);
    renderFrame();
    expect(panel()).toHaveAttribute('data-detent', 'collapsed');
    act(() => {
      window.history.pushState(null, '', '#/official-guidance');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(screen.getByRole('dialog', { name: 'official-guidance' })).toBeInTheDocument();
    expect(panel()).toHaveAttribute('data-detent', 'half');
  });
});

describe('AppFrame page chrome stays outside the stage', () => {
  it.each([320, 1024, 1280])('keeps the Disclaimer_Banner and header outside the stage and panels at %i px', (width) => {
    setViewport(width);
    renderFrame({ pickMode: 'start' });
    const banner = screen.getByTestId('disclaimer-banner');
    const header = screen.getByTestId('app-header');
    for (const el of [banner, header]) {
      expect(stage()).not.toContainElement(el);
      expect(panel()).not.toContainElement(el);
    }
  });

  it('keeps the map and the attribution strip outside the panels, clear of the Tab_Bar on compact', () => {
    setViewport(320);
    renderFrame();
    const strip = screen.getByTestId('attribution-strip');
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(panel()).not.toContainElement(strip);
    expect(panel()).not.toContainElement(screen.getByTestId('map-stub'));
    expect(strip.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Map area' }).style.getPropertyValue('--map-attribution-bottom')).not.toBe(
      '0px',
    );
  });

  it('puts the map attribution at the stage bottom on wide', () => {
    setViewport(1024);
    renderFrame();
    expect(screen.getByRole('region', { name: 'Map area' }).style.getPropertyValue('--map-attribution-bottom')).toBe(
      '0px',
    );
    expect(panel()).not.toContainElement(screen.getByTestId('map-stub'));
  });
});
