import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Emergency911Panel } from '../Emergency911Panel';
import { ProfileMenu } from './ProfileMenu';
import { SCREEN_TITLES } from './screens';
import { ScreenFrame } from './ScreenFrame';
import { useNavigation } from './useNavigation';

/** Validates: Requirements 6.5, 6.6, 6.8, 16.7 */

const start = (hash: string) => window.history.replaceState(null, '', hash);

beforeEach(() => start('#/tab/map'));

describe('useNavigation', () => {
  it('pushes tabs onto the hash', () => {
    const { result } = renderHook(() => useNavigation());
    expect(result.current.tab).toBe('map');
    act(() => result.current.openTab('route'));
    expect(window.location.hash).toBe('#/tab/route');
    expect(result.current.tab).toBe('route');
    expect(result.current.screen).toBeNull();
  });

  it('walks history Back and Forward in order', async () => {
    const { result } = renderHook(() => useNavigation());
    act(() => result.current.openTab('route'));
    act(() => result.current.openTab('safety'));
    act(() => result.current.openScreen('official-guidance'));
    expect(window.location.hash).toBe('#/official-guidance');

    act(() => window.history.back());
    await waitFor(() => expect(result.current.screen).toBeNull());
    expect(result.current.tab).toBe('safety');
    act(() => window.history.back());
    await waitFor(() => expect(result.current.tab).toBe('route'));
    act(() => window.history.forward());
    await waitFor(() => expect(result.current.tab).toBe('safety'));
    act(() => window.history.forward());
    await waitFor(() => expect(result.current.screen).toBe('official-guidance'));
  });

  it('back() pops to the previous entry and backLabel names it', async () => {
    const { result } = renderHook(() => useNavigation());
    act(() => result.current.openTab('route'));
    act(() => result.current.openScreen('route-details'));
    expect(result.current.backLabel).toBe('Route');
    act(() => result.current.back());
    await waitFor(() => expect(window.location.hash).toBe('#/tab/route'));
    await waitFor(() => expect(result.current.screen).toBeNull());
  });

  it('back() after a deep link falls back to the parent tab without leaving the app', () => {
    start('#/data-sources');
    const { result } = renderHook(() => useNavigation());
    expect(result.current.screen).toBe('data-sources');
    expect(result.current.backLabel).toBe('More');
    act(() => result.current.back());
    expect(window.location.hash).toBe('#/tab/more');
    expect(result.current.tab).toBe('more');
    expect(result.current.screen).toBeNull();
  });

  it('backLabel for a screen opened from another screen is that screen title', () => {
    const { result } = renderHook(() => useNavigation());
    act(() => result.current.openScreen('community'));
    act(() => result.current.openScreen('submit-report'));
    expect(result.current.backLabel).toBe(SCREEN_TITLES.community);
  });

  it('opens the demo console and treats it as the More tab', () => {
    const { result } = renderHook(() => useNavigation());
    act(() => result.current.openDemo());
    expect(window.location.hash).toBe('#/demo');
    expect(result.current.demo).toBe(true);
    expect(result.current.tab).toBe('more');
  });
});

describe('Escape layers', () => {
  function Harness({ onScreenClose }: { onScreenClose: () => void }) {
    const [screenOpen, setScreenOpen] = useState(true);
    return (
      <div>
        {screenOpen && (
          <ScreenFrame
            title="Guidance"
            onClose={() => {
              onScreenClose();
              setScreenOpen(false);
            }}
          >
            <p>body</p>
          </ScreenFrame>
        )}
        <Emergency911Panel />
        <ProfileMenu me={null} />
      </div>
    );
  }

  it('closes the profile menu, then the 911 panel, then the pushed screen', async () => {
    const user = userEvent.setup();
    const onScreenClose = vi.fn();
    render(<Harness onScreenClose={onScreenClose} />);

    await user.click(screen.getByRole('button', { name: /Emergency: call 911/ }));
    await user.click(screen.getByRole('button', { name: /^Profile/ }));
    expect(screen.getByTestId('emergency-911-body')).toBeInTheDocument();
    expect(screen.getByTestId('profile-panel')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('profile-panel')).not.toBeInTheDocument();
    expect(screen.getByTestId('emergency-911-body')).toBeInTheDocument();
    expect(onScreenClose).not.toHaveBeenCalled();

    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('emergency-911-body')).not.toBeInTheDocument();
    expect(onScreenClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Guidance' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(onScreenClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
