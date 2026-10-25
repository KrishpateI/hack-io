import { useSyncExternalStore } from 'react';
import { palettes, type Appearance } from './tokens';

/** The user's choice; `system` follows the OS color scheme. */
export type AppearanceSetting = 'system' | 'light' | 'dark';

export const APPEARANCE_STORAGE_KEY = 'safepath.appearance';
const DARK_QUERY = '(prefers-color-scheme: dark)';

/** Resolve a setting to a concrete Appearance. */
export function resolveAppearance(setting: AppearanceSetting, systemDark: boolean): Appearance {
  if (setting === 'light') return 'light';
  if (setting === 'dark') return 'dark';
  return systemDark ? 'dark' : 'light';
}

/** Any value other than 'light' or 'dark' (including null, corrupt data) parses as `system`. */
export function parseSetting(raw: unknown): AppearanceSetting {
  return raw === 'light' || raw === 'dark' ? raw : 'system';
}

export function serializeSetting(setting: AppearanceSetting): string {
  return setting;
}

// In-memory fallback used when localStorage is unavailable or throws.
let memorySetting: AppearanceSetting = 'system';

export function readSetting(): AppearanceSetting {
  try {
    const raw = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
    return raw === null ? memorySetting : parseSetting(raw);
  } catch {
    return memorySetting;
  }
}

export function writeSetting(setting: AppearanceSetting): void {
  memorySetting = setting;
  try {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, serializeSetting(setting));
  } catch {
    // Storage blocked: the in-memory value still applies for this session.
  }
}

function systemPrefersDark(): boolean {
  // A missing matchMedia is treated as dark (the original look).
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true;
  try {
    return window.matchMedia(DARK_QUERY).matches;
  } catch {
    return true;
  }
}

/** Apply a resolved Appearance to the root element and the single theme-color meta. */
export function applyAppearance(root: HTMLElement, appearance: Appearance): void {
  root.setAttribute('data-appearance', appearance);
  root.style.colorScheme = appearance;
  const doc = root.ownerDocument;
  let meta = doc.querySelector('meta[name="theme-color"]');
  if (!meta) {
    meta = doc.createElement('meta');
    meta.setAttribute('name', 'theme-color');
    doc.head.appendChild(meta);
  }
  meta.setAttribute('content', palettes[appearance].base);
}

// ---- store ----
type Snapshot = { setting: AppearanceSetting; appearance: Appearance };
let snapshot: Snapshot | null = null;
const listeners = new Set<() => void>();

function compute(): Snapshot {
  const setting = readSetting();
  return { setting, appearance: resolveAppearance(setting, systemPrefersDark()) };
}

function refresh(): void {
  const next = compute();
  const changed = !snapshot || snapshot.setting !== next.setting || snapshot.appearance !== next.appearance;
  if (changed) snapshot = next;
  if (typeof document !== 'undefined') applyAppearance(document.documentElement, next.appearance);
  if (changed) listeners.forEach((l) => l());
}

function getSnapshot(): Snapshot {
  if (!snapshot) snapshot = compute();
  return snapshot;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

let initialized = false;

/** Apply the stored setting now and follow OS and cross-tab changes. Call before first render. */
export function initAppearance(): void {
  refresh();
  if (initialized || typeof window === 'undefined') return;
  initialized = true;
  if (typeof window.matchMedia === 'function') {
    try {
      const mql = window.matchMedia(DARK_QUERY);
      if (typeof mql.addEventListener === 'function') mql.addEventListener('change', refresh);
      else if (typeof mql.addListener === 'function') mql.addListener(refresh);
    } catch {
      // No system subscription available; the setting still applies.
    }
  }
  window.addEventListener('storage', (e) => {
    if (e.key === null || e.key === APPEARANCE_STORAGE_KEY) refresh();
  });
}

export function useAppearance(): {
  setting: AppearanceSetting;
  appearance: Appearance;
  setSetting: (setting: AppearanceSetting) => void;
} {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return {
    setting: snap.setting,
    appearance: snap.appearance,
    setSetting: (setting) => {
      writeSetting(setting);
      refresh();
    },
  };
}
