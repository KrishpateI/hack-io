import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OverlaySnapshotLike } from '../services/mapData';
import { OVERLAY_REFETCH_DEBOUNCE_MS, useMapOverlays, type OverlayFetcher } from './useMapOverlays';

const fcOf = (features: unknown[]) => ({ type: 'FeatureCollection' as const, features });

function snapshot(incidentId: string, version = 1, fireCount = 1): OverlaySnapshotLike {
  return {
    incident_id: incidentId,
    overlay_version: version,
    restrictions: fcOf([
      {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [[-87.63, 41.88], [-87.629, 41.881]] },
        properties: { restriction_id: `${incidentId}-c1`, edge_id: 'e1', simulated: true },
      },
    ]),
    hazards: {
      flood: fcOf([]),
      debris: fcOf([]),
      fire: fcOf(
        Array.from({ length: fireCount }, (_, i) => ({
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: [[[-87.63, 41.88], [-87.62, 41.88], [-87.62, 41.89], [-87.63, 41.88]]] },
          properties: { notice_id: `${incidentId}-n${i}`, simulated: true, label: 'SIMULATED fire perimeter (not an official instruction)' },
        })),
      ),
    },
  };
}

interface Deferred { resolve: (v: OverlaySnapshotLike) => void; reject: (e: unknown) => void; id: string }

/** A fetcher whose responses the test resolves by hand. */
function manualFetcher() {
  const calls: Deferred[] = [];
  const fetcher = vi.fn<OverlayFetcher>(
    (id) =>
      new Promise<OverlaySnapshotLike>((resolve, reject) => {
        calls.push({ resolve, reject, id });
      }),
  );
  return { fetcher, calls };
}

const flush = () => act(async () => { await Promise.resolve(); });
const restriction = (incidentId: string, type: string, id: string, version: number, edgeIds: string[] = []) => ({
  event_id: `ev-${incidentId}-${type}-${id}-${version}`,
  incident_id: incidentId,
  type,
  payload: { restriction_id: id, kind: 'closure', edge_ids: edgeIds, simulated: true, overlay_version: version },
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('useMapOverlays', () => {
  it('loads the snapshot for the selected incident with an abort signal', async () => {
    const { fetcher, calls } = manualFetcher();
    const { result } = renderHook(() => useMapOverlays('inc-a', { fetcher }));
    expect(fetcher).toHaveBeenCalledWith('inc-a', expect.any(AbortSignal));
    expect(result.current.status).toBe('loading');
    calls[0].resolve(snapshot('inc-a'));
    await flush();
    expect(result.current.status).toBe('loaded');
    expect(result.current.inputs.hazards.fire?.features).toHaveLength(1);
    expect(result.current.inputs.restrictions?.features).toHaveLength(1);
  });

  it('discards a late response for the previous incident and a response for another incident', async () => {
    const { fetcher, calls } = manualFetcher();
    const { result, rerender } = renderHook(({ id }) => useMapOverlays(id, { fetcher }), { initialProps: { id: 'inc-a' } });
    rerender({ id: 'inc-b' });
    expect(fetcher.mock.calls[0][1].aborted).toBe(true);
    calls[0].resolve(snapshot('inc-a'));
    await flush();
    expect(result.current.merged.restrictions.features).toHaveLength(0);
    // the server answered with a payload for a different incident: ignored
    calls[1].resolve(snapshot('inc-a', 9));
    await flush();
    expect(result.current.inputs.hazards.fire?.features).toHaveLength(0);
    expect(result.current.snapshot).toBeNull();
    act(() => result.current.refresh());
    calls[2].resolve(snapshot('inc-b'));
    await flush();
    expect(result.current.snapshot?.incident_id).toBe('inc-b');
    expect(result.current.merged.restrictions.features[0].properties?.restriction_id).toBe('inc-b-c1');
  });

  it('clears everything when the selection becomes null', async () => {
    const { fetcher, calls } = manualFetcher();
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useMapOverlays(id, { fetcher }), {
      initialProps: { id: 'inc-a' as string | null },
    });
    calls[0].resolve(snapshot('inc-a'));
    await flush();
    expect(result.current.inputs.hazards.fire?.features).toHaveLength(1);
    rerender({ id: null });
    expect(result.current.inputs.hazards.fire?.features).toHaveLength(0);
    expect(result.current.inputs.restrictions?.features).toHaveLength(0);
    expect(result.current.snapshot).toBeNull();
    expect(result.current.status).toBe('idle');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('refetches once, debounced, after restriction and notice events for the incident', async () => {
    const { fetcher, calls } = manualFetcher();
    const { result } = renderHook(() => useMapOverlays('inc-a', { fetcher }));
    calls[0].resolve(snapshot('inc-a', 1));
    await flush();

    act(() => {
      result.current.handleEvent(restriction('inc-a', 'restriction_added', 'r-new', 2, ['e9']));
      result.current.handleEvent({ type: 'notice_published', incident_id: 'inc-a', payload: {} });
      result.current.handleEvent(restriction('inc-b', 'restriction_added', 'r-other', 3, ['e7']));
      result.current.handleEvent({ type: 'report_created', incident_id: 'inc-a', payload: {} });
    });
    // the new restriction has no geometry yet: its edges are pending for the interim fallback
    expect(result.current.pendingEdgeIds).toEqual(['e9']);
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERLAY_REFETCH_DEBOUNCE_MS - 1); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetcher).toHaveBeenCalledTimes(2);

    const next = snapshot('inc-a', 2);
    next.restrictions = fcOf([
      ...(next.restrictions?.features ?? []),
      { type: 'Feature', geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] }, properties: { restriction_id: 'r-new', edge_id: 'e9' } },
    ]);
    calls[1].resolve(next);
    await flush();
    expect(result.current.pendingEdgeIds).toEqual([]);
    expect(result.current.merged.restrictions.features).toHaveLength(2);
  });

  it('applies a restriction_removed event immediately, before the refetch', async () => {
    const { fetcher, calls } = manualFetcher();
    const { result } = renderHook(() => useMapOverlays('inc-a', { fetcher }));
    calls[0].resolve(snapshot('inc-a', 4));
    await flush();
    act(() => result.current.handleEvent(restriction('inc-a', 'restriction_removed', 'inc-a-c1', 5)));
    expect(result.current.merged.restrictions.features).toHaveLength(0);
  });

  it('retries with backoff after a failure and keeps the last good snapshot', async () => {
    const { fetcher, calls } = manualFetcher();
    const { result } = renderHook(() => useMapOverlays('inc-a', { fetcher, random: () => 1 }));
    calls[0].reject(new Error('network'));
    await flush();
    expect(result.current.status).toBe('failed');
    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); }); // backoffDelay(0) with random 1 = 1000 ms
    expect(fetcher).toHaveBeenCalledTimes(2);
    calls[1].resolve(snapshot('inc-a', 1));
    await flush();
    expect(result.current.status).toBe('loaded');

    // a failing refetch keeps what is drawn and schedules another retry
    act(() => result.current.refresh());
    calls[2].reject(new Error('503'));
    await flush();
    expect(result.current.inputs.hazards.fire?.features).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
});
