import {
  createContext,
  useContext,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

/**
 * The panel content (tab body or pushed screen) renders once into a persistent detached `div`
 * through `createPortal`. React keeps the subtree mounted, so state survives when the active frame
 * changes (Side_Panel <-> Bottom_Sheet). The frame shows the content by appending that `div` into
 * its body slot (`PanelSlot`) in a layout effect. If a control inside had focus before the move,
 * focus returns to the element with the same `id`; otherwise to the panel title.
 */

interface HostState {
  host: HTMLDivElement;
  /** Id of the focused element inside the host, or null when focus is elsewhere. */
  focusedId: string | null;
}

const HostContext = createContext<HostState | null>(null);

function createHost(): HostState {
  const host = document.createElement('div');
  host.className = 'min-w-0';
  host.setAttribute('data-panel-body', '');
  const state: HostState = { host, focusedId: null };
  host.addEventListener('focusin', (e) => {
    const t = e.target as HTMLElement | null;
    state.focusedId = t && t.id ? t.id : null;
  });
  host.addEventListener('focusout', () => {
    // A focusout while the host is detached is the move itself; keep the id for restoration.
    if (host.isConnected) state.focusedId = null;
  });
  return state;
}

export interface PanelBodyProps {
  children: ReactNode;
}

/**
 * Owns the persistent host and portals `children` into it. Render it once, above the frames
 * (outside any component that changes type with the viewport tier). Frames render `PanelSlot`
 * inside this component's `slots` subtree via context.
 */
export function PanelBody({ children, frame }: PanelBodyProps & { frame?: ReactNode }) {
  const [state] = useState(createHost);
  return (
    <HostContext.Provider value={state}>
      {createPortal(children, state.host)}
      {frame}
    </HostContext.Provider>
  );
}

export interface PanelSlotProps {
  /** Visible or visually hidden title of the panel; receives focus when the focused control is gone. */
  title: string;
  className?: string;
}

/**
 * Body slot of a frame. Appends the persistent host in a layout effect and restores focus after
 * the move. Must render inside `PanelBody`'s `frame`.
 */
export function PanelSlot({ title, className = '' }: PanelSlotProps) {
  const state = useContext(HostContext);
  if (!state) throw new Error('PanelSlot must render inside PanelBody');
  const slot = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useLayoutEffect(() => {
    const el = slot.current;
    if (!el) return;
    const { host } = state;
    const wanted = state.focusedId;
    const moved = host.parentNode !== el;
    if (moved) el.appendChild(host);
    if (!moved || wanted === null) return;
    if (host.contains(document.activeElement) && document.activeElement !== document.body) return;
    const target = host.querySelector<HTMLElement>(`[id="${wanted.replace(/(["\\])/g, '\\$1')}"]`);
    if (target) target.focus();
    else titleRef.current?.focus();
  });

  // The panel title is the accessible name of the slot wrapper, which is the focus fallback.
  return (
    <div
      ref={titleRef}
      id={titleId}
      role="group"
      aria-label={title}
      tabIndex={-1}
      data-focus-target="panel"
      className={`focus:outline-none ${className}`}
    >
      <div ref={slot} data-testid="panel-slot" />
    </div>
  );
}
