import { useState, type ReactNode } from 'react';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readShortcuts, SHORTCUTS_KEY, writeShortcuts } from '../ios/preferences';
import { AppFrame } from './AppFrame';
import { TAB_IDS, TAB_TITLES, type TabId } from './navigation';
import { MoreTab } from './tabs/MoreTab';
import { TabHeading } from './TabHeading';
import { useKeyboardShortcuts } from './useKeyboardShortcuts';
import { useNavigation } from './useNavigation';

/** Validates: Requirements 16.4, 16.5, 16.10, 14.8 */

// MapLibre needs WebGL; the wiring under test does not depend on the map itself.
vi.mock('../../maps/MapView', () => ({ MapView: () => <div data-testid="map-stub" /> }));

beforeEach(() => {
  window.localStorage.clear();
  writeShortcuts(true); // preferences keeps an in-memory copy that localStorage.clear() does not reset
  window.localStorage.clear();
  window.history.replaceState(null, '', '#/tab/map');
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '#/tab/map');
});

describe('useKeyboardShortcuts: global handler registration (Req 16.5)', () => {
  it('adds a keydown listener only while enabled and removes it when disabled or unmounted', () => {
    const add = vi.spyOn(document, 'addEventListener');
    const remove = vi.spyOn(document, 'removeEventListener');
    const keydownCalls = (spy: typeof add) => spy.mock.calls.filter(([type]) => type === 'keydown').length;

    const { rerender, unmount } = renderHook(
      ({ enabled }) => useKeyboardShortcuts({ enabled, onSelectTab: vi.fn() }),
      { initialProps: { enabled: false } },
    );
    expect(keydownCalls(add)).toBe(0);

    rerender({ enabled: true });
    expect(keydownCalls(add)).toBe(1);

    rerender({ enabled: false });
    expect(keydownCalls(remove)).toBe(1);
    expect(keydownCalls(add)).toBe(1);

    rerender({ enabled: true });
    expect(keydownCalls(add)).toBe(2);
    unmount();
    expect(keydownCalls(remove)).toBe(2);
  });

  it('does not select a tab while disabled', async () => {
    const onSelectTab = vi.fn();
    renderHook(() => useKeyboardShortcuts({ enabled: false, onSelectTab }));
    await userEvent.keyboard('g3');
    expect(onSelectTab).not.toHaveBeenCalled();
  });
});

describe('useKeyboardShortcuts: key handling', () => {
  it('selects the tab for g then a digit and announces it', async () => {
    const onSelectTab = vi.fn();
    const { result } = renderHook(() => useKeyboardShortcuts({ enabled: true, onSelectTab }));
    expect(result.current.announcement).toBe('');
    await userEvent.keyboard('g3');
    expect(onSelectTab).toHaveBeenCalledExactlyOnceWith('community');
    expect(result.current.announcement).toBe('Community tab');
  });

  it('ignores the sequence while focus is inside a text input', async () => {
    const onSelectTab = vi.fn();
    render(<input aria-label="Name" />);
    renderHook(() => useKeyboardShortcuts({ enabled: true, onSelectTab }));
    await userEvent.type(screen.getByLabelText('Name'), 'g3');
    expect(onSelectTab).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Name')).toHaveValue('g3');
  });

  it('ignores the sequence in a textarea and a select', async () => {
    const onSelectTab = vi.fn();
    render(
      <>
        <textarea aria-label="Notes" />
        <select aria-label="Kind">
          <option>a</option>
        </select>
      </>,
    );
    renderHook(() => useKeyboardShortcuts({ enabled: true, onSelectTab }));
    await userEvent.type(screen.getByLabelText('Notes'), 'g2');
    screen.getByLabelText('Kind').focus();
    await userEvent.keyboard('g2');
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  it.each(['Control', 'Alt', 'Meta'])('ignores the digit while %s is held', async (modifier) => {
    const onSelectTab = vi.fn();
    renderHook(() => useKeyboardShortcuts({ enabled: true, onSelectTab }));
    await userEvent.keyboard(`g{${modifier}>}2{/${modifier}}`);
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  it('ignores a digit without a preceding g', async () => {
    const onSelectTab = vi.fn();
    renderHook(() => useKeyboardShortcuts({ enabled: true, onSelectTab }));
    await userEvent.keyboard('3');
    expect(onSelectTab).not.toHaveBeenCalled();
  });
});

/**
 * The wiring AppShell does around the frame: the setting seeded from storage, the hook driven by
 * `useNavigation().openTab`, a TabHeading at the top of each tab, the More tab switch, and the
 * live region. The rest of AppShell (auth, incidents, routing) is not needed to exercise it.
 */
function Harness() {
  const nav = useNavigation();
  const [enabled, setEnabled] = useState(readShortcuts);
  const shortcuts = useKeyboardShortcuts({
    enabled,
    onSelectTab: nav.openTab,
    contentSelector: '[data-panel-body]',
  });
  const onShortcutsChange = (next: boolean) => {
    setEnabled(next);
    writeShortcuts(next);
  };
  const renderTab = Object.fromEntries(
    TAB_IDS.map((id) => [
      id,
      (): ReactNode => (
        <>
          <TabHeading tab={id} />
          {id === 'route' && <input aria-label="Start latitude" />}
          {id === 'more' && (
            <MoreTab
              me={null}
              appearanceSetting="system"
              onAppearanceChange={vi.fn()}
              shortcutsEnabled={enabled}
              onShortcutsChange={onShortcutsChange}
            />
          )}
        </>
      ),
    ]),
  ) as Record<TabId, () => ReactNode>;
  return (
    <>
      <AppFrame renderTab={renderTab} renderScreen={() => null} />
      <div aria-live="polite" className="sr-only" data-testid="shortcut-announcer">
        {shortcuts.announcement}
      </div>
    </>
  );
}

const announcer = () => screen.getByTestId('shortcut-announcer');
const shortcutSwitch = () => screen.getByRole('switch', { name: 'Keyboard shortcuts' });

describe('shortcut wiring around AppFrame', () => {
  it('marks the Map_View region as the map focus target', () => {
    render(<Harness />);
    const map = screen.getByRole('region', { name: 'Map area' });
    expect(map).toHaveAttribute('data-focus-target', 'map');
  });

  it('g then 3 opens Community, moves focus to its heading, and announces the tab', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(announcer()).toBeEmptyDOMElement();
    await user.keyboard('g3');
    expect(window.location.hash).toBe('#/tab/community');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Community' })).toHaveFocus());
    expect(announcer()).toHaveTextContent('Community tab');
  });

  it.each(TAB_IDS.map((id, i) => [i + 1, id] as const))('g then %i opens the %s tab', async (digit, id) => {
    const user = userEvent.setup();
    render(<Harness />);
    // Start from a different tab so that the shortcut is a real change.
    await user.keyboard(`g${digit === 1 ? 2 : 1}`);
    await user.keyboard(`g${digit}`);
    expect(window.location.hash).toBe(`#/tab/${id}`);
    await waitFor(() => expect(screen.getByRole('heading', { name: TAB_TITLES[id] })).toHaveFocus());
    expect(announcer()).toHaveTextContent(`${TAB_TITLES[id]} tab`);
  });

  it('does not change tab or announce while typing in a text input', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.keyboard('g2');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Route' })).toHaveFocus());
    const input = screen.getByLabelText('Start latitude');
    await user.click(input);
    await user.keyboard('g3');
    expect(input).toHaveValue('g3');
    expect(window.location.hash).toBe('#/tab/route');
    expect(announcer()).toHaveTextContent('Route tab');
  });

  it('does not change tab when a modifier is held', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.keyboard('g{Control>}3{/Control}');
    expect(window.location.hash).toBe('#/tab/map');
    expect(announcer()).toBeEmptyDOMElement();
  });

  it('does nothing when the setting is stored as off', async () => {
    writeShortcuts(false);
    const user = userEvent.setup();
    render(<Harness />);
    await user.keyboard('g3');
    expect(window.location.hash).toBe('#/tab/map');
    expect(announcer()).toBeEmptyDOMElement();
  });
});

describe('Keyboard shortcuts setting (Req 16.4, 16.5)', () => {
  it('is on by default', () => {
    window.history.replaceState(null, '', '#/tab/more');
    render(<Harness />);
    expect(shortcutSwitch()).toBeChecked();
  });

  it('switching it off stops the shortcuts at once and switching it on restores them', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#/tab/more');
    render(<Harness />);
    await user.click(shortcutSwitch());
    expect(shortcutSwitch()).not.toBeChecked();
    expect(window.localStorage.getItem(SHORTCUTS_KEY)).toBe('off');

    await user.keyboard('g1');
    expect(window.location.hash).toBe('#/tab/more');

    await user.click(shortcutSwitch());
    expect(shortcutSwitch()).toBeChecked();
    expect(window.localStorage.getItem(SHORTCUTS_KEY)).toBe('on');
    await user.keyboard('g1');
    expect(window.location.hash).toBe('#/tab/map');
  });

  it('persists across a remount: off stays off, with no global handler registered', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#/tab/more');
    const first = render(<Harness />);
    await user.click(shortcutSwitch());
    first.unmount();

    const add = vi.spyOn(document, 'addEventListener');
    render(<Harness />);
    expect(shortcutSwitch()).not.toBeChecked();
    expect(add.mock.calls.filter(([type]) => type === 'keydown')).toHaveLength(0);
    await user.keyboard('g1');
    expect(window.location.hash).toBe('#/tab/more');
  });

  it('persists across a remount: on stays on', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#/tab/more');
    const first = render(<Harness />);
    await user.click(shortcutSwitch());
    await user.click(shortcutSwitch());
    first.unmount();

    render(<Harness />);
    expect(shortcutSwitch()).toBeChecked();
    await act(async () => {
      await user.keyboard('g1');
    });
    expect(window.location.hash).toBe('#/tab/map');
  });
});
