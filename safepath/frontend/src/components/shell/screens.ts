/** Registry of the full-screen pages/dialogs the shell can open (Req 34.4-34.6). Pure data. */
export const SCREEN_IDS = [
  'disaster-selection',
  'route-details',
  'submit-report',
  'community',
  'incident-overview',
  'safety-check-in',
  'official-guidance',
  'data-sources',
] as const;

export type ScreenId = (typeof SCREEN_IDS)[number];

export const SCREEN_TITLES: Record<ScreenId, string> = {
  'disaster-selection': 'Select a disaster',
  'route-details': 'Route details',
  'submit-report': 'Report a hazard',
  community: 'Community communications',
  'incident-overview': 'Incident overview',
  'safety-check-in': 'Safety check-in',
  'official-guidance': 'Official guidance',
  'data-sources': 'Data sources and limitations',
};

export const isScreenId = (v: unknown): v is ScreenId => typeof v === 'string' && (SCREEN_IDS as readonly string[]).includes(v);

export const screenTitle = (id: ScreenId): string => SCREEN_TITLES[id];

export const screenHash = (id: ScreenId): string => `#/${id}`;

/** Parse a location hash such as "#/route-details"; returns null for the map (no screen open). */
export function screenFromHash(hash: string): ScreenId | null {
  const id = hash.replace(/^#\/?/, '');
  if (id === 'sources') return 'data-sources'; // documented alias: #/sources
  return isScreenId(id) ? id : null;
}
