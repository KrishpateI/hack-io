import { useCallback, useEffect, useState } from 'react';
import {
  formatLocation,
  parseLocation,
  selectedTab,
  TAB_TITLES,
  type NavLocation,
  type TabId,
} from './navigation';
import { SCREEN_TITLES, type ScreenId } from './screens';

/** Dispatched after the app changes the history entry itself, so every hook instance re-reads the hash. */
const NAVIGATE_EVENT = 'safepath:navigate';

const currentHash = (): string => (typeof window === 'undefined' ? '' : window.location.hash);

const locationTitle = (loc: NavLocation): string => {
  switch (loc.kind) {
    case 'tab':
      return TAB_TITLES[loc.tab];
    case 'screen':
      return SCREEN_TITLES[loc.screen];
    case 'demo':
      return 'Demo console';
  }
};

/** The `spFrom` hash stored by the app on the current history entry, if the entry was pushed by the app. */
function readFrom(): string | null {
  const state: unknown = typeof window === 'undefined' ? null : window.history.state;
  if (state && typeof state === 'object' && 'spFrom' in state) {
    const from = (state as { spFrom: unknown }).spFrom;
    return typeof from === 'string' ? from : null;
  }
  return null;
}

/** Parent tab used as the fallback destination for back(). */
const parentTab = (loc: NavLocation): TabId => (loc.kind === 'tab' ? 'map' : selectedTab(loc));

/** Tab, screen, and demo navigation over the location hash. Replaces `useScreenNav`. */
export function useNavigation() {
  const [hash, setHash] = useState<string>(currentHash);
  // Re-read on every render path that follows a push so backLabel sees the new history.state.
  const location = parseLocation(hash);

  useEffect(() => {
    const sync = () => setHash(currentHash());
    window.addEventListener('hashchange', sync);
    window.addEventListener('popstate', sync);
    window.addEventListener(NAVIGATE_EVENT, sync);
    sync();
    return () => {
      window.removeEventListener('hashchange', sync);
      window.removeEventListener('popstate', sync);
      window.removeEventListener(NAVIGATE_EVENT, sync);
    };
  }, []);

  const push = useCallback((loc: NavLocation) => {
    const next = formatLocation(loc);
    const prev = currentHash();
    if (next === prev) return;
    window.history.pushState({ spFrom: prev }, '', next);
    setHash(next);
    // pushState fires no event; tell any other hook instance (AppShell and AppFrame each use one).
    window.dispatchEvent(new Event(NAVIGATE_EVENT));
  }, []);

  const openTab = useCallback((tab: TabId) => push({ kind: 'tab', tab }), [push]);
  const openScreen = useCallback((screen: ScreenId) => push({ kind: 'screen', screen }), [push]);
  const openDemo = useCallback(() => push({ kind: 'demo' }), [push]);

  const back = useCallback(() => {
    if (readFrom() !== null) {
      window.history.back();
      return;
    }
    const fallback = formatLocation({ kind: 'tab', tab: parentTab(parseLocation(currentHash())) });
    window.history.replaceState(null, '', fallback);
    setHash(fallback);
    window.dispatchEvent(new Event(NAVIGATE_EVENT));
  }, []);

  const from = readFrom();
  const backLabel =
    from !== null ? locationTitle(parseLocation(from)) : TAB_TITLES[parentTab(location)];

  return {
    location,
    tab: selectedTab(location),
    screen: location.kind === 'screen' ? location.screen : null,
    demo: location.kind === 'demo',
    openTab,
    openScreen,
    openDemo,
    back,
    backLabel,
  };
}
