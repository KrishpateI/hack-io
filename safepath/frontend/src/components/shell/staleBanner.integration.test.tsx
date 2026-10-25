import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRealtime } from '../../hooks/useRealtime';
import { ConnectivityStatus, formatSyncTime, STALE_HEADER_MESSAGE } from './ConnectivityStatus';

/**
 * Stale-data banner integration (Req 25.5, 44.10): the real useRealtime hook + WebSocketClient +
 * RealtimeController drive ConnectivityStatus; only the browser WebSocket and fetch are faked.
 */

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  close() {}
  static latest(): FakeWebSocket {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }
}

function Harness() {
  const rt = useRealtime('inc_1');
  return <ConnectivityStatus status={rt.status} lastSyncedAt={rt.lastSyncedAt} stale={rt.stale} />;
}

const T0 = Date.UTC(2026, 0, 1, 12, 0, 0);
const flush = () => act(() => vi.advanceTimersByTimeAsync(0));

describe('stale-data banner', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ events: [] }) })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('shows the warning with the last update time within 10 s of disconnect and clears on reconnect', async () => {
    render(<Harness />);
    const ws = FakeWebSocket.latest();
    expect(ws.url).toContain('/ws/incidents/inc_1');

    act(() => ws.onopen?.());
    await flush();
    expect(screen.getByTestId('connectivity-status')).toHaveTextContent('Connected');
    expect(screen.queryByTestId('stale-indicator')).not.toBeInTheDocument();

    // Last received update at T0 + 2 s.
    await act(() => vi.advanceTimersByTimeAsync(2000));
    const lastUpdate = Date.now();
    act(() =>
      ws.onmessage?.({
        data: JSON.stringify({ event_id: 'evt_01', type: 'report_created', incident_id: 'inc_1', payload: { report_id: 'r1' } }),
      }),
    );

    // Connection drops.
    act(() => ws.onclose?.());
    expect(screen.getByTestId('connectivity-status')).toHaveTextContent('Reconnecting');
    expect(screen.queryByTestId('stale-indicator')).not.toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    const banner = screen.getByTestId('stale-indicator');
    expect(banner).toHaveTextContent(STALE_HEADER_MESSAGE);
    expect(banner).toHaveAttribute('role', 'alert');
    expect(screen.getByTestId('last-synced')).toHaveTextContent(`Last synced: ${formatSyncTime(lastUpdate)}`);

    // The controller has been retrying; once a socket opens and catch-up succeeds, the banner clears.
    const retry = FakeWebSocket.latest();
    expect(retry).not.toBe(ws);
    act(() => retry.onopen?.());
    await flush();
    expect(screen.getByTestId('connectivity-status')).toHaveTextContent('Connected');
    expect(screen.queryByTestId('stale-indicator')).not.toBeInTheDocument();
    expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('since=evt_01'), expect.anything());
  });
});
