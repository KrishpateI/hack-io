/**
 * Stored preferences for Side_Panel mode and Keyboard_Shortcuts.
 * Reads parse invalid values to defaults; a throwing localStorage falls back
 * to in-memory values for the session.
 */

// Defined locally; layout.ts (owned by another task) exports the same union.
export type PanelMode = 'default' | 'expanded' | 'collapsed';
export type ShortcutsSetting = 'on' | 'off';

export const PANEL_MODE_KEY = 'safepath.panelMode';
export const SHORTCUTS_KEY = 'safepath.shortcuts';

const memory = new Map<string, string>();

function getItem(key: string): string | null {
  try {
    const value = window.localStorage.getItem(key);
    if (value !== null) return value;
    return memory.get(key) ?? null;
  } catch {
    return memory.get(key) ?? null;
  }
}

function setItem(key: string, value: string): void {
  memory.set(key, value);
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // In-memory value already set.
  }
}

export function parsePanelMode(raw: string | null | undefined): PanelMode {
  return raw === 'expanded' || raw === 'collapsed' || raw === 'default' ? raw : 'default';
}

export function parseShortcuts(raw: string | null | undefined): boolean {
  return raw === 'off' ? false : true;
}

export function readPanelMode(): PanelMode {
  return parsePanelMode(getItem(PANEL_MODE_KEY));
}

export function writePanelMode(mode: PanelMode): void {
  setItem(PANEL_MODE_KEY, mode);
}

/** Returns true when shortcuts are enabled (default). */
export function readShortcuts(): boolean {
  return parseShortcuts(getItem(SHORTCUTS_KEY));
}

export function writeShortcuts(enabled: boolean): void {
  setItem(SHORTCUTS_KEY, enabled ? 'on' : 'off');
}
