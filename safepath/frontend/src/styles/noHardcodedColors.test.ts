import { describe, expect, it } from 'vitest';

/**
 * Hard-coded color guard (Requirements 4.6, 15.2).
 *
 * Screens take colors from the Design_Tokens (Tailwind token classes or CSS variables), so no
 * source under `src/components`, `src/pages`, or `src/maps/*.tsx` may contain a `#rrggbb` literal.
 * Map layer colors live in `src/maps/layers` (excluded). The reusable `src/components/ios` files
 * are stricter: they may not contain any color literal (hex, rgb(), hsl(), and so on).
 */

const components = import.meta.glob<string>('../components/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
});
const pages = import.meta.glob<string>('../pages/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
});
const maps = import.meta.glob<string>('../maps/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
});

const isTestFile = (path: string) => /\.(test|spec)\.tsx?$/.test(path) || path.endsWith('.d.ts');

const scanned = Object.entries({ ...components, ...pages, ...maps }).filter(([p]) => !isTestFile(p));
const iosFiles = scanned.filter(([p]) => p.startsWith('../components/ios/'));

/** Remove comments so documentation may mention example values. */
export function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1');
}

const HEX_RRGGBB = /#[0-9a-fA-F]{6}\b/g;
/** Any hex color (3, 4, 6, or 8 digits) or a CSS color function. */
const ANY_COLOR = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/g;

export function findHex(src: string): string[] {
  return stripComments(src).match(HEX_RRGGBB) ?? [];
}

export function findAnyColor(src: string): string[] {
  return stripComments(src).match(ANY_COLOR) ?? [];
}

describe('color literal detectors', () => {
  it('flags #rrggbb literals and ignores comments', () => {
    expect(findHex('const a = "#0b1220";')).toEqual(['#0b1220']);
    expect(findHex('// #0b1220\n/* #ffffff */ const a = 1;')).toEqual([]);
    expect(findHex('<a href="#main-content">Skip</a>')).toEqual([]);
  });

  it('flags short hex and color functions for the reusable component files', () => {
    expect(findAnyColor('x = "#fff"')).toEqual(['#fff']);
    expect(findAnyColor('x = "rgba(0, 0, 0, 0.5)"')).toEqual(['rgba(']);
    expect(findAnyColor('className="bg-surface text-text-primary"')).toEqual([]);
  });
});

describe('no hard-coded colors outside the Design_Tokens (Req 4.6, 15.2)', () => {
  it('scans components, pages, and map components, and excludes map layers and tests', () => {
    expect(scanned.length).toBeGreaterThan(0);
    expect(iosFiles.length).toBeGreaterThan(0);
    expect(scanned.some(([p]) => p.startsWith('../pages/'))).toBe(true);
    expect(scanned.some(([p]) => p.startsWith('../maps/'))).toBe(true);
    expect(scanned.some(([p]) => p.includes('/maps/layers/'))).toBe(false);
    expect(scanned.some(([p]) => isTestFile(p))).toBe(false);
  });

  it.each(scanned)('%s has no #rrggbb literal', (_path, source) => {
    expect(findHex(source)).toEqual([]);
  });

  it.each(iosFiles)('%s (reusable ios component) has no color literal at all', (_path, source) => {
    expect(findAnyColor(source)).toEqual([]);
  });
});
