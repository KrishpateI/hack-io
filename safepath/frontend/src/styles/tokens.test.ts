import { describe, expect, it } from 'vitest';
import { palette, type PaletteKey } from './tokens';

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
