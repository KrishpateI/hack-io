import { describe, expect, it } from 'vitest';
import { palette, palettes, extraColors, type Appearance, type PaletteKey } from './tokens';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** WCAG 2.x relative luminance and contrast ratio. */
function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
export function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT_KEYS: PaletteKey[] = [
  'text',
  'textMuted',
  'route',
  'routeAlt',
  'closure',
  'unverified',
  'verified',
];
const BACKGROUND_KEYS: PaletteKey[] = ['base', 'surface', 'surfaceRaised'];

const pairs = TEXT_KEYS.flatMap((fg) => BACKGROUND_KEYS.map((bg) => [fg, bg] as const));

describe('palette contrast (Req 1.5, 36.3)', () => {
  it('computes known WCAG reference values', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrast('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
  });

  it.each(pairs)('%s on %s is at least 4.5:1', (fg, bg) => {
    expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps every palette color covered as text or background', () => {
    const covered = new Set<PaletteKey>([...TEXT_KEYS, ...BACKGROUND_KEYS, 'border']);
    expect([...covered].sort()).toEqual(Object.keys(palette).sort());
  });
});

const APPEARANCES: Appearance[] = ['light', 'dark'];
const SURFACE_KEYS: PaletteKey[] = ['base', 'surface', 'surfaceRaised'];

function hue(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  if (d === 0) return 0;
  let h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  h *= 60;
  return h < 0 ? h + 360 : h;
}
function hueDistance(a: string, b: string): number {
  const d = Math.abs(hue(a) - hue(b));
  return Math.min(d, 360 - d);
}

describe('Property 3: contrast in both Appearances (Req 4.5, 12.1, 12.2, 12.3)', () => {
  const textCases = APPEARANCES.flatMap((a) =>
    TEXT_KEYS.flatMap((fg) => SURFACE_KEYS.map((bg) => [a, fg, bg] as const)),
  );

  it.each(textCases)('%s: %s on %s is at least 4.5:1', (a, fg, bg) => {
    expect(contrast(palettes[a][fg], palettes[a][bg])).toBeGreaterThanOrEqual(4.5);
  });

  const focusCases = APPEARANCES.flatMap((a) => SURFACE_KEYS.map((bg) => [a, bg] as const));

  it.each(focusCases)('%s: focus ring (route) on %s is at least 3:1', (a, bg) => {
    expect(contrast(palettes[a].route, palettes[a][bg])).toBeGreaterThanOrEqual(3);
  });

  it.each(focusCases)('%s: control border on %s is at least 3:1', (a, bg) => {
    expect(contrast(extraColors[a].controlBorder, palettes[a][bg])).toBeGreaterThanOrEqual(3);
  });

  const glassCases = APPEARANCES.flatMap((a) => TEXT_KEYS.map((fg) => [a, fg] as const));

  it.each(glassCases)('%s: %s on opaque glass fallback is at least 4.5:1', (a, fg) => {
    expect(contrast(palettes[a][fg], extraColors[a].glassFallback)).toBeGreaterThanOrEqual(4.5);
  });

  it('light and dark palettes define identical keys', () => {
    expect(Object.keys(palettes.light).sort()).toEqual(Object.keys(palettes.dark).sort());
  });

  it.each(APPEARANCES)('%s: route, closure, unverified, verified are distinct hues', (a) => {
    const keys = ['route', 'closure', 'unverified', 'verified'] as const;
    for (let i = 0; i < keys.length; i++) {
      for (let j = i + 1; j < keys.length; j++) {
        expect(hueDistance(palettes[a][keys[i]], palettes[a][keys[j]])).toBeGreaterThanOrEqual(20);
      }
    }
  });
});

const css = readFileSync(resolve(__dirname, 'tailwind.css'), 'utf8');

describe('tailwind.css structure', () => {
  it('defines --target-min of 44px for coarse and 32px for fine pointers', () => {
    expect(css).toMatch(/@media \(pointer: coarse\)\s*\{\s*:root\s*\{\s*--target-min:\s*44px/);
    expect(css).toMatch(/@media \(pointer: fine\)\s*\{\s*:root\s*\{\s*--target-min:\s*32px/);
  });

  it('guards backdrop-filter with @supports and keeps an opaque fallback', () => {
    expect(css).toMatch(/@supports \(backdrop-filter:[^)]*\)[\s\S]*?backdrop-filter:/);
    expect(css).toMatch(/\.glass\s*\{\s*background-color:\s*var\(--glass-fallback\)/);
  });
});