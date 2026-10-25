// Feature: ios-style-ui-redesign, Property 1: Appearance resolution and pre-paint agreement
// **Validates: Requirements 5.2, 5.3, 5.4**
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import initSource from '../../public/appearance-init.js?raw';
import { parseSetting, resolveAppearance } from './appearance';

/** Evaluate the pre-paint script against the jsdom document with a stubbed window. */
function runInit(stored: string | null, systemDark: boolean): string | null {
  const root = document.documentElement;
  root.removeAttribute('data-appearance');
  const fakeWindow = {
    localStorage: { getItem: () => stored },
    matchMedia: () => ({ matches: systemDark }),
  };
  new Function('window', 'document', initSource)(fakeWindow, document);
  return root.getAttribute('data-appearance');
}

const storedArb = fc.oneof(
  fc.constant<string | null>(null),
  fc.constantFrom('light', 'dark', 'system', 'Light', 'DARK', '', ' dark', 'null'),
  fc.string(),
);

describe('Property 1: Appearance resolution and pre-paint agreement', () => {
  it('resolveAppearance follows system only for the system setting, and init script agrees', () => {
    fc.assert(
      fc.property(storedArb, fc.boolean(), (stored, systemDark) => {
        const setting = parseSetting(stored);
        const result = resolveAppearance(setting, systemDark);
        const expected = setting === 'system' ? (systemDark ? 'dark' : 'light') : setting;
        expect(result).toBe(expected);
        expect(runInit(stored, systemDark)).toBe(result);
      }),
      { numRuns: 100 },
    );
  });
});
