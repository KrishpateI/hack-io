import {
  useEffect,
  useMemo,
  useRef,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import { useSheetDrag } from './hooks';
import { detentHeights, nextDetent, type Detent } from './layout';

export interface BottomSheetProps {
  detent: Detent;
  onDetentChange: (next: Detent) => void;
  /** Vertical space (px) the sheet may occupy; feeds `detentHeights`. */
  available: number;
  /** Map element made `inert` (with `aria-hidden` fallback) while the sheet is Full. */
  mapRef?: RefObject<HTMLElement | null>;
  /** Called when the sheet enters or leaves Full. */
  onFullChange?: (isFull: boolean) => void;
  children?: ReactNode;
  className?: string;
}

const UP: Record<Detent, Detent> = { collapsed: 'half', half: 'full', full: 'full' };
const DOWN: Record<Detent, Detent> = { collapsed: 'collapsed', half: 'collapsed', full: 'half' };
const DRAG_SLOP = 4;

/** iOS-style bottom sheet with three detents, a grabber button, and glass chrome. */
export function BottomSheet({
  detent,
  onDetentChange,
  available,
  mapRef,
  onFullChange,
  children,
  className = '',
}: BottomSheetProps) {
  const heights = useMemo(() => detentHeights(available), [available]);
  const { dragHeight, dragging, animate, handlers } = useSheetDrag({
    heights,
    detent,
    onDetentChange,
  });
  const grabberRef = useRef<HTMLButtonElement>(null);
  const prevDetent = useRef<Detent>(detent);
  const downY = useRef<number | null>(null);
  const moved = useRef(false);
  const isFull = detent === 'full';

  // Full: the map is inert, with aria-hidden for engines without `inert`.
  useEffect(() => {
    const map = mapRef?.current;
    if (!map) return;
    if (isFull) {
      map.setAttribute('inert', '');
      map.setAttribute('aria-hidden', 'true');
    } else {
      map.removeAttribute('inert');
      map.removeAttribute('aria-hidden');
    }
    return () => {
      map.removeAttribute('inert');
      map.removeAttribute('aria-hidden');
    };
  }, [isFull, mapRef]);

  useEffect(() => {
    onFullChange?.(isFull);
  }, [isFull, onFullChange]);

  // Leaving Full returns focus to the grabber.
  useEffect(() => {
    if (prevDetent.current === 'full' && detent !== 'full') grabberRef.current?.focus();
    prevDetent.current = detent;
  }, [detent]);

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      onDetentChange(UP[detent]);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      onDetentChange(DOWN[detent]);
    }
  };

  const height = dragHeight ?? heights[detent];
  const transition = animate && !dragging;

  return (
    <section
      role="region"
      aria-label="SafePath panel"
      data-detent={detent}
      className={`glass flex flex-col overflow-hidden rounded-t-[var(--radius-card)] border border-b-0 border-border-subtle shadow-[var(--shadow-card)] ${
        transition ? 'transition-[height] duration-[var(--duration-base)] ease-[var(--ease-standard)]' : ''
      } ${className}`}
      style={{ height }}
    >
      <button
        ref={grabberRef}
        type="button"
        aria-label={`Resize panel, ${detent}`}
        onKeyDown={onKeyDown}
        onClick={() => {
          if (moved.current) {
            moved.current = false;
            return;
          }
          onDetentChange(nextDetent(detent));
        }}
        onPointerDown={(e) => {
          downY.current = e.clientY;
          moved.current = false;
          handlers.onPointerDown(e);
        }}
        onPointerMove={(e) => {
          if (downY.current !== null && Math.abs(e.clientY - downY.current) > DRAG_SLOP) {
            moved.current = true;
          }
          handlers.onPointerMove(e);
        }}
        onPointerUp={(e) => {
          downY.current = null;
          handlers.onPointerUp(e);
        }}
        onPointerCancel={(e) => {
          downY.current = null;
          moved.current = false;
          handlers.onPointerCancel(e);
        }}
        className="flex min-h-[var(--target-min)] w-full shrink-0 touch-none items-center justify-center"
      >
        <span
          aria-hidden="true"
          data-testid="sheet-grabber"
          className="h-1.5 w-9 rounded-full bg-control-border"
        />
      </button>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-[env(safe-area-inset-bottom)]">
        {children}
      </div>
    </section>
  );
}
