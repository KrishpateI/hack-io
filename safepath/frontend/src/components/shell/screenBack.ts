import { createContext } from 'react';

/**
 * Back navigation for a pushed screen. `ScreenRouter` provides it so every page rendered through
 * it shows a `Back to <title>` control without each page threading the props (Req 6.6).
 * Explicit `backTitle`/`onBack` props on `ScreenFrame` take precedence.
 */
export interface ScreenBack {
  backTitle?: string;
  onBack?: () => void;
}

export const ScreenBackContext = createContext<ScreenBack>({});
