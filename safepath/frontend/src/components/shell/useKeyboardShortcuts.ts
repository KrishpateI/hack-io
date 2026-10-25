import { useEffect, useRef, useState } from 'react';
import { TAB_IDS, TAB_TITLES, type TabId } from './navigation';

/** Window, in ms, in which a digit must follow "g" (Requirement 16.1). */
export const SHORTCUT_WINDOW_MS = 1500;

export interface ShortcutState {
  /** Timestamp of the last accepted "g", or null when not armed. */
  armedAt: number | null;
}

export type ShortcutEvent = {
  key: string;
  at: number;
  ctrl: boolean;
  alt: boolean;
  meta: boolean;
  inEditable: boolean;
};

export const INITIAL_SHORTCUT_STATE: ShortcutState = { armedAt: null };

/**
 * Pure reducer for the "g then 1-5" sequence.
 * Ignored events (disabled, modifier held, editable focus) leave the armed state untouched,
 * except that disabling clears it. Accepted events: "g" arms; any other key disarms, and a digit
 * 1-5 within the window while armed also yields the tab.
 */
export function shortcutReducer(
  state: ShortcutState,
  event: ShortcutEvent,
  enabled: boolean,
): { state: ShortcutState; tab: TabId | null } {
  if (!enabled) return { state: INITIAL_SHORTCUT_STATE, tab: null };
  if (event.ctrl || event.alt || event.meta || event.inEditable) return { state, tab: null };

  if (event.key === 'g') return { state: { armedAt: event.at }, tab: null };

  const armed = state.armedAt !== null && event.at - state.armedAt >= 0 && event.at - state.armedAt <= SHORTCUT_WINDOW_MS;
  if (armed && /^[1-5]$/.test(event.key)) {
    return { state: INITIAL_SHORTCUT_STATE, tab: TAB_IDS[Number(event.key) - 1] };
  }
  return { state: INITIAL_SHORTCUT_STATE, tab: null };
}

/** True when the element is a text input, textarea, select, or editable region. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as Element).tagName !== 'string') return false;
  const el = target as HTMLElement;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = ((el as HTMLInputElement).type || 'text').toLowerCase();
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'image', 'file', 'color'].includes(type);
  }
  return el.isContentEditable || el.getAttribute('contenteditable') === '' || el.getAttribute('contenteditable') === 'true';
}

export interface UseKeyboardShortcutsOptions {
  enabled: boolean;
  onSelectTab: (tab: TabId) => void;
  /** CSS selector of the tab content container whose first heading receives focus. Default: "main". */
  contentSelector?: string;
}

/**
 * Registers a global keydown handler only while enabled (Requirement 16.5). On a match it selects the
 * tab, moves focus to the first heading of the new tab content, and returns announcer text for an
 * `aria-live="polite"` region (Requirement 16.10).
 */
export function useKeyboardShortcuts({
  enabled,
  onSelectTab,
  contentSelector = 'main',
}: UseKeyboardShortcutsOptions): { announcement: string } {
  const [announcement, setAnnouncement] = useState('');
  const onSelectRef = useRef(onSelectTab);
  useEffect(() => {
    onSelectRef.current = onSelectTab;
  });

  useEffect(() => {
    if (!enabled) return undefined;
    let state = INITIAL_SHORTCUT_STATE;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const onKeyDown = (e: KeyboardEvent) => {
      const result = shortcutReducer(
        state,
        {
          key: e.key,
          at: e.timeStamp,
          ctrl: e.ctrlKey,
          alt: e.altKey,
          meta: e.metaKey,
          inEditable: isEditableTarget(e.target),
        },
        true,
      );
      state = result.state;
      const tab = result.tab;
      if (!tab) return;
      e.preventDefault();
      onSelectRef.current(tab);
      setAnnouncement(`${TAB_TITLES[tab]} tab`);
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const root = document.querySelector(contentSelector);
        const heading = root?.querySelector<HTMLElement>('h1, h2, h3, h4, h5, h6, [role="heading"]');
        if (heading) {
          if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
          heading.focus();
        }
      }, 0);
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (timer) clearTimeout(timer);
    };
  }, [enabled, contentSelector]);

  return { announcement };
}
