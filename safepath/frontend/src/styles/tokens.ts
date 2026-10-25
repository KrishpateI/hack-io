/**
 * Design tokens. Dark navy/charcoal base; cyan/blue routes, red closures,
 * orange unverified, green verified resources. Mirrored in tailwind.css @theme.
 * Contrast against `surface` is checked by tests (>= 4.5:1, Req 36.3).
 */
export const palette = {
  base: '#0b1220',
  surface: '#111a2e',
  surfaceRaised: '#1a2540',
  border: '#2c3a5c',
  text: '#e6edf7',
  textMuted: '#a9b6cf',
  route: '#22d3ee',
  routeAlt: '#60a5fa',
  closure: '#f87171',
  unverified: '#fb923c',
  verified: '#4ade80',
} as const;

export type PaletteKey = keyof typeof palette;

/** Resolved Appearance (the setting `system` resolves to one of these). */
export type Appearance = 'light' | 'dark';

/** Light palette: same keys as the dark `palette`; mirrored in tailwind.css. */
export const lightPalette: Record<PaletteKey, string> = {
  base: '#f2f2f7',
  surface: '#ffffff',
  surfaceRaised: '#f9f9fb',
  border: '#c6c6cc',
  text: '#1c1c1e',
  textMuted: '#55596a',
  route: '#0369a1',
  routeAlt: '#1d4ed8',
  closure: '#b91c1c',
  unverified: '#b45309',
  verified: '#13773a',
};

export const palettes: Record<Appearance, Record<PaletteKey, string>> = {
  dark: palette,
  light: lightPalette,
};

/** Extra per-Appearance tokens (control border >= 3:1; opaque glass fallback). */
export const extraColors = {
  dark: { controlBorder: '#7d8ba8', glass: 'rgba(17, 26, 46, 0.72)', glassFallback: '#111a2e' },
  light: { controlBorder: '#85858a', glass: 'rgba(255, 255, 255, 0.72)', glassFallback: '#ffffff' },
} as const;

/** Non-color tokens, mirrored as CSS variables in tailwind.css. */
export const radius = { card: 14, control: 12 } as const;
export const space = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24 } as const;
export const type = {
  largeTitle: 28,
  title: 20,
  headline: 17,
  body: 15,
  caption: 12,
} as const;
export const motion = { fast: 150, base: 250, slow: 350 } as const;
export const targetMin = { coarse: 44, fine: 32 } as const;

export const DISCLAIMER_TEXT =
  'SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies.';
