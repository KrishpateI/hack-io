// Feature: ios-style-ui-redesign, Property 2: Stored preferences round-trip and reject corrupt values
// **Validates: Requirements 5.3, 7.4, 16.4**
import { beforeEach, describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  PANEL_MODE_KEY,
  SHORTCUTS_KEY,
  readPanelMode,
  readShortcuts,
  writePanelMode,
  writeShortcuts,
  type PanelMode,
} from './preferences';
import {
  APPEARANCE_STORAGE_KEY,
  readSetting,
  writeSetting,
  type AppearanceSetting,
} from '../../styles/appearance';

const RUNS = { numRuns: 100 };
const settings = fc.constantFrom<AppearanceSetting>('system', 'light', 'dark');
const modes = fc.constantFrom<PanelMode>('default', 'expanded', 'collapsed');

describe('Property 2: Stored preferences round-trip and reject corrupt values', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('round-trips every valid value', () => {
    fc.assert(
      fc.property(settings, modes, fc.boolean(), (s, m, k) => {
        writeSetting(s);
        writePanelMode(m);
        writeShortcuts(k);
        expect(readSetting()).toBe(s);
        expect(readPanelMode()).toBe(m);
        expect(readShortcuts()).toBe(k);
      }),
      RUNS,
    );
  });

  it('reads arbitrary stored strings as a valid member', () => {
    fc.assert(
      fc.property(fc.string(), (raw) => {
        window.localStorage.setItem(APPEARANCE_STORAGE_KEY, raw);
        window.localStorage.setItem(PANEL_MODE_KEY, raw);
        window.localStorage.setItem(SHORTCUTS_KEY, raw);
        const expectSetting = raw === 'light' || raw === 'dark' ? raw : 'system';
        const expectMode = ['default', 'expanded', 'collapsed'].includes(raw) ? raw : 'default';
        expect(readSetting()).toBe(expectSetting);
        expect(readPanelMode()).toBe(expectMode);
        expect(readShortcuts()).toBe(raw !== 'off');
      }),
      RUNS,
    );
  });
});
