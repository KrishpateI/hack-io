import { useCallback, useEffect, useState } from 'react';
import { screenFromHash, screenHash, type ScreenId } from './screens';

/** Minimal hash-based screen navigation: `#/route-details` opens a screen, empty hash shows the map. */
export function useScreenNav() {
  const [screen, setScreen] = useState<ScreenId | null>(() =>
    typeof window === 'undefined' ? null : screenFromHash(window.location.hash),
  );

  useEffect(() => {
    const onHash = () => setScreen(screenFromHash(window.location.hash));
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const open = useCallback((id: ScreenId) => {
    window.location.hash = screenHash(id);
    setScreen(id);
  }, []);

  const close = useCallback(() => {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    setScreen(null);
  }, []);

  return { screen, open, close };
}
