/**
 * Artifact layer status store and retry (map-legend-layers-fix, task 3.7).
 *
 * **Validates: Requirements 2.10, 3.5**
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MapLayers } from './MapLayers';
import {
  ARTIFACT_RETRY_BASE_MS,
  ARTIFACT_RETRY_CAP_MS,
  artifactRetryDelay,
  clearLayerCache,
  getArtifactStatus,
  useArtifactLayers,
  useArtifactStatus,
} from './layerData';

const station = {
  type: 'FeatureCollection',
  features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [-87.63, 41.88] }, properties: { name: 'S' } }],
};
const two = { type: 'FeatureCollection', features: [station.features[0], station.features[0]] };

const ALL_ON = { buildings: true, transit: true, simulated_station: true };

describe('artifact layer status store', () => {
  let stationFails = true;
  let calls: string[] = [];
  const stationCalls = () => calls.filter((u) => u.includes('/api/layers/simulated_station')).length;

  beforeEach(() => {
    clearLayerCache();
    vi.useFakeTimers();
    calls = [];
    stationFails = true;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push(String(url));
      if (String(url).includes('/api/layers/simulated_station')) {
        return stationFails ? new Response('unavailable', { status: 503 }) : new Response(JSON.stringify(station), { status: 200 });
      }
      return new Response(JSON.stringify(two), { status: 200 });
    }));
  });

  afterEach(() => {
    clearLayerCache();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const makeManager = () => ({ setArtifactLayer: vi.fn() });

  it('starts idle and marks a failing layer as failed', async () => {
    expect(getArtifactStatus().simulated_station).toEqual({ status: 'idle' });
    const m = makeManager();
    renderHook(() => useArtifactLayers(m as unknown as MapLayers, ALL_ON));
    const status = renderHook(() => useArtifactStatus());
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(status.result.current.simulated_station).toEqual({ status: 'failed' });
  });

  it('a failure never blocks the other layers', async () => {
    const m = makeManager();
    renderHook(() => useArtifactLayers(m as unknown as MapLayers, ALL_ON));
    await vi.advanceTimersByTimeAsync(10);
    const s = getArtifactStatus();
    expect(s.buildings).toEqual({ status: 'loaded', count: 2 });
    expect(s.cta_stations).toEqual({ status: 'loaded', count: 2 });
    expect(s.simulated_station?.status).toBe('failed');
    const pushed = m.setArtifactLayer.mock.calls.map((c) => c[0]);
    expect(pushed.sort()).toEqual(['buildings', 'cta_stations']);
  });

  it('schedules a retry that succeeds, reporting loaded with a count', async () => {
    const m = makeManager();
    renderHook(() => useArtifactLayers(m as unknown as MapLayers, ALL_ON));
    await vi.advanceTimersByTimeAsync(10);
    expect(stationCalls()).toBe(1);
    stationFails = false;
    await vi.advanceTimersByTimeAsync(ARTIFACT_RETRY_BASE_MS + 10);
    expect(stationCalls()).toBe(2);
    expect(getArtifactStatus().simulated_station).toEqual({ status: 'loaded', count: 1 });
    expect(m.setArtifactLayer).toHaveBeenCalledWith('simulated_station', station);
  });

  it('retries immediately on the browser online event', async () => {
    const m = makeManager();
    renderHook(() => useArtifactLayers(m as unknown as MapLayers, ALL_ON));
    await vi.advanceTimersByTimeAsync(10);
    stationFails = false;
    window.dispatchEvent(new Event('online'));
    await vi.advanceTimersByTimeAsync(1);
    expect(stationCalls()).toBe(2);
    expect(getArtifactStatus().simulated_station).toEqual({ status: 'loaded', count: 1 });
  });

  it('retries when the toggle is turned on again', async () => {
    const m = makeManager();
    const { rerender } = renderHook(({ v }) => useArtifactLayers(m as unknown as MapLayers, v), {
      initialProps: { v: ALL_ON as Record<string, boolean> },
    });
    await vi.advanceTimersByTimeAsync(10);
    rerender({ v: { ...ALL_ON, simulated_station: false } });
    await vi.advanceTimersByTimeAsync(10);
    expect(stationCalls()).toBe(1);
    stationFails = false;
    rerender({ v: { ...ALL_ON } });
    await vi.advanceTimersByTimeAsync(1);
    expect(stationCalls()).toBe(2);
    expect(getArtifactStatus().simulated_station?.status).toBe('loaded');
  });

  it('clearLayerCache resets statuses and cancels pending retries', async () => {
    const m = makeManager();
    const { unmount } = renderHook(() => useArtifactLayers(m as unknown as MapLayers, ALL_ON));
    await vi.advanceTimersByTimeAsync(10);
    clearLayerCache();
    expect(getArtifactStatus().simulated_station).toEqual({ status: 'idle' });
    await vi.advanceTimersByTimeAsync(ARTIFACT_RETRY_CAP_MS);
    expect(stationCalls()).toBe(1);
    unmount();
  });

  it('retry delay grows from 5 s and is capped at 5 min', () => {
    expect(artifactRetryDelay(0, () => 1)).toBe(ARTIFACT_RETRY_BASE_MS);
    expect(artifactRetryDelay(0, () => 0)).toBe(ARTIFACT_RETRY_BASE_MS / 2);
    expect(artifactRetryDelay(3, () => 1)).toBe(ARTIFACT_RETRY_BASE_MS * 8);
    expect(artifactRetryDelay(50, () => 1)).toBe(ARTIFACT_RETRY_CAP_MS);
  });
});
