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

export const DISCLAIMER_TEXT =
  'SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies.';
