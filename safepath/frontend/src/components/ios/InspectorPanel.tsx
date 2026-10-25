import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { useDismissibleLayer } from './hooks';
import { INSPECTOR_RIGHT } from './layout';

export interface InspectorPanelProps {
  open: boolean;
  /** Visible heading of the inspected feature. */
  title: string;
  onClose: () => void;
  children?: ReactNode;
  /** Panel width in px (from stageLayout().inspectorWidth, 340 to 400). Defaults to 360. */
  width?: number;
  className?: string;
}

/** The Map_View region carries `data-focus-target="map"`; any `data-focus-target` is the fallback. */
function findMapRegion(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  const el =
    document.querySelector<HTMLElement>('[data-focus-target="map"]') ??
    document.querySelector<HTMLElement>('[role="region"][data-focus-target]');
  if (el && !el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  return el;
}

/**
 * Desktop-tier trailing glass card. Positioned 60 px from the right and 40 px from the
 * bottom of the stage so MapLibre's navigation control (top right) and attribution
 * (bottom right) stay uncovered. Escape closes it and focus returns to the Map_View region.
 */
export function InspectorPanel({
  open,
  title,
  onClose,
  children,
  width = 360,
  className = '',
}: InspectorPanelProps) {
  const openerRef = useRef<HTMLElement | null>(null);

  // Resolve the map region before useDismissibleLayer's effect reads the ref.
  useLayoutEffect(() => {
    if (open) openerRef.current = findMapRegion();
  }, [open]);

  useDismissibleLayer(open, onClose, openerRef);

  if (!open) return null;

  const close = () => {
    onClose();
    const target = findMapRegion();
    if (target) target.focus();
  };

  return (
    <section
      role="region"
      aria-label="Map feature details"
      data-testid="inspector-panel"
      className={`glass absolute bottom-[40px] top-3 z-20 flex flex-col overflow-hidden rounded-[var(--radius-card)] border border-border-subtle text-text-primary shadow-[var(--shadow-card)] ${className}`}
      style={{ width, right: INSPECTOR_RIGHT }}
    >
      <div className="flex min-h-[var(--target-min)] items-center justify-between gap-2 border-b border-border-subtle px-4">
        <h2 className="m-0 min-w-0 truncate text-[length:var(--text-body)] font-semibold">{title}</h2>
        <button
          type="button"
          onClick={close}
          aria-label="Close map feature details"
          className="inline-flex min-h-[var(--target-min)] min-w-[var(--target-min)] items-center justify-center rounded-[var(--radius-control)] text-text-muted transition-opacity duration-[var(--duration-fast)] active:opacity-50"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false">
            <path d="M3 3l10 10M13 3L3 13" />
          </svg>
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
    </section>
  );
}
