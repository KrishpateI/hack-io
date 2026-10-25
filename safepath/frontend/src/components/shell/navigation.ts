import { isScreenId, type ScreenId } from './screens';

/** Navigation grammar: five tabs, eight pushed screens, and the demo console. Pure data and functions. */
export const TAB_IDS = ['map', 'route', 'community', 'safety', 'more'] as const;

export type TabId = (typeof TAB_IDS)[number];

export const TAB_TITLES: Record<TabId, string> = {
  map: 'Map',
  route: 'Route',
  community: 'Community',
  safety: 'Safety',
  more: 'More',
};

/** The parent tab the Tab_List highlights while a screen is open. */
export const SCREEN_TAB: Record<ScreenId, TabId> = {
  'disaster-selection': 'map',
  'route-details': 'route',
  'submit-report': 'safety',
  community: 'community',
  'incident-overview': 'more',
  'safety-check-in': 'safety',
  'official-guidance': 'safety',
  'data-sources': 'more',
};

export type NavLocation =
  | { kind: 'tab'; tab: TabId }
  | { kind: 'screen'; screen: ScreenId }
  | { kind: 'demo' };

const isTabId = (v: string): v is TabId => (TAB_IDS as readonly string[]).includes(v);

const MAP: NavLocation = { kind: 'tab', tab: 'map' };

/** Total: never throws; unknown or malformed input resolves to the Map tab. */
export function parseLocation(hash: string): NavLocation {
  if (typeof hash !== 'string') return { ...MAP };
  const body = hash.replace(/^#\/?/, '');
  if (body.startsWith('tab/')) {
    const tab = body.slice(4);
    return isTabId(tab) ? { kind: 'tab', tab } : { ...MAP };
  }
  if (body === 'demo') return { kind: 'demo' };
  if (body === 'sources') return { kind: 'screen', screen: 'data-sources' }; // documented alias: #/sources
  if (isScreenId(body)) return { kind: 'screen', screen: body };
  return { ...MAP };
}

export function formatLocation(loc: NavLocation): string {
  switch (loc.kind) {
    case 'tab':
      return `#/tab/${loc.tab}`;
    case 'screen':
      return `#/${loc.screen}`;
    case 'demo':
      return '#/demo';
  }
}

/** The tab the Tab_List highlights for a location. */
export function selectedTab(loc: NavLocation): TabId {
  switch (loc.kind) {
    case 'tab':
      return loc.tab;
    case 'screen':
      return SCREEN_TAB[loc.screen];
    case 'demo':
      return 'more';
  }
}
