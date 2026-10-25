import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import mainSource from '../main.tsx?raw';
import { palettes } from './tokens';

type Mod = typeof import('./appearance');

const KEY = 'safepath.appearance';

/** Controllable matchMedia stub. */
function installMatchMedia(initialDark: boolean) {
  let dark = initialDark;
  const listeners = new Set<() => void>();
  window.matchMedia = vi.fn().mockImplementation(() => ({
    get matches() {
      return dark;
    },
    addEventListener: (_: string, l: () => void) => listeners.add(l),
    removeEventListener: (_: string, l: () => void) => listeners.delete(l),
  })) as unknown as typeof window.matchMedia;
  return {
    set(next: boolean) {
      dark = next;
      listeners.forEach((l) => l());
    },
  };
}

async function load(): Promise<Mod> {
  vi.resetModules();
  return import('./appearance');
}

const root = () => document.documentElement;
const metas = () => document.querySelectorAll('meta[name="theme-color"]');

describe('Appearance_Setting', () => {
  beforeEach(() => {
    window.localStorage.clear();
    root().removeAttribute('data-appearance');
    root().style.colorScheme = '';
    metas().forEach((m) => m.remove());
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('System follows matchMedia changes without reload', async () => {
    const mq = installMatchMedia(false);
    const m = await load();
    m.initAppearance();
    expect(root().getAttribute('data-appearance')).toBe('light');
    act(() => mq.set(true));
    expect(root().getAttribute('data-appearance')).toBe('dark');
    act(() => mq.set(false));
    expect(root().getAttribute('data-appearance')).toBe('light');
  });

  it('Light and Dark apply immediately and persist', async () => {
    installMatchMedia(true);
    const m = await load();
    m.initAppearance();
    const { result } = renderHook(() => m.useAppearance());
    act(() => result.current.setSetting('light'));
    expect(result.current.appearance).toBe('light');
    expect(root().getAttribute('data-appearance')).toBe('light');
    expect(root().style.colorScheme).toBe('light');
    expect(window.localStorage.getItem(KEY)).toBe('light');

    act(() => result.current.setSetting('dark'));
    expect(result.current.appearance).toBe('dark');
    expect(root().getAttribute('data-appearance')).toBe('dark');
    expect(window.localStorage.getItem(KEY)).toBe('dark');

    // A fresh load restores the persisted choice.
    const m2 = await load();
    m2.initAppearance();
    expect(m2.readSetting()).toBe('dark');
  });

  it('applies the stored setting before createRoot renders in main.tsx', async () => {
    installMatchMedia(false);
    window.localStorage.setItem(KEY, 'dark');
    const m = await load();
    m.initAppearance();
    expect(root().getAttribute('data-appearance')).toBe('dark');

    const initAt = mainSource.indexOf('initAppearance();');
    const renderAt = mainSource.indexOf('createRoot(');
    expect(initAt).toBeGreaterThan(-1);
    expect(renderAt).toBeGreaterThan(-1);
    expect(initAt).toBeLessThan(renderAt);
  });

  it('falls back to System for a corrupt stored value', async () => {
    const mq = installMatchMedia(true);
    window.localStorage.setItem(KEY, '{not-a-setting');
    const m = await load();
    expect(m.readSetting()).toBe('system');
    expect(m.parseSetting(null)).toBe('system');
    expect(m.parseSetting('DARK')).toBe('system');
    m.initAppearance();
    expect(root().getAttribute('data-appearance')).toBe('dark');
    act(() => mq.set(false));
    expect(root().getAttribute('data-appearance')).toBe('light');
  });

  it('falls back to memory when localStorage throws', async () => {
    installMatchMedia(false);
    const m = await load();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(m.readSetting()).toBe('system');
    expect(() => m.writeSetting('dark')).not.toThrow();
    expect(m.readSetting()).toBe('dark');

    m.initAppearance();
    expect(root().getAttribute('data-appearance')).toBe('dark');
  });

  it('treats a missing matchMedia as dark', async () => {
    // @ts-expect-error simulate absence
    window.matchMedia = undefined;
    const m = await load();
    m.initAppearance();
    expect(root().getAttribute('data-appearance')).toBe('dark');
  });

  it('keeps a single theme-color meta and updates it with the Appearance', async () => {
    installMatchMedia(true);
    const m = await load();
    m.initAppearance();
    expect(metas()).toHaveLength(1);
    expect(metas()[0].getAttribute('content')).toBe(palettes.dark.base);

    const { result } = renderHook(() => m.useAppearance());
    act(() => result.current.setSetting('light'));
    expect(metas()).toHaveLength(1);
    expect(metas()[0].getAttribute('content')).toBe(palettes.light.base);
  });
});
