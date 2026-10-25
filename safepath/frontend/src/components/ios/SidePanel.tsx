import { useCallback, useState, type ReactNode } from 'react';
import { Chevron } from './icons';
import type { PanelMode, StageLayout } from './layout';
import { readPanelMode, writePanelMode } from './preferences';

export interface SidePanelProps {
  mode: PanelMode;
  onModeChange: (mode: PanelMode) => void;
  /** Geometry from `stageLayout`; supplies the width and whether Expand is allowed. */
  layout: StageLayout;
  /** Optional always-visible slot above the body (e.g. a TabList, in rail mode too). */
  nav?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Panel mode state that is restored from, and written to, stored preferences. */
export function useStoredPanelMode(): [PanelMode, (mode: PanelMode) => void] {
  const [mode, setMode] = useState<PanelMode>(() => readPanelMode());
  const update = useCallback((next: PanelMode) => {
    writePanelMode(next);
    setMode(next);
  }, []);
  return [mode, update];
}

const EXPAND_HINT_ID = 'side-panel-expand-hint';

/** Translucent iOS-style left panel with its own scroll area. */
export function SidePanel({
  mode,
  onModeChange,
  layout,
  nav,
  children,
  className = '',
}: SidePanelProps) {
  const collapsed = mode === 'collapsed';
  const expanded = mode === 'expanded';
  const expandDisabled = !layout.canExpand && !expanded;

  const button =
    'flex min-h-[var(--target-min)] min-w-[var(--target-min)] items-center justify-center rounded-[var(--radius-control)] border border-control-border px-2 text-[length:var(--text-caption)] text-text-primary transition-opacity duration-[var(--duration-fast)] active:opacity-70 disabled:opacity-50';

  return (
    <section
      role="region"
      aria-label="SafePath panel"
      data-mode={mode}
      style={{ width: layout.panelWidth }}
      className={`glass flex h-full min-h-0 shrink-0 flex-col overflow-hidden rounded-[var(--radius-card)] border border-border-subtle text-text-primary shadow-[var(--shadow-card)] transition-[width] duration-[var(--duration-base)] ease-[var(--ease-standard)] ${className}`}
    >
      <div
        className={`flex items-center gap-2 p-2 ${collapsed ? 'flex-col' : 'justify-end'}`}
      >
        {collapsed ? (
          <button
            type="button"
            className={button}
            aria-label="Restore panel"
            onClick={() => onModeChange('default')}
          >
            <Chevron direction="right" />
          </button>
        ) : (
          <>
            {expanded ? (
              <button
                type="button"
                className={button}
                aria-label="Restore panel size"
                onClick={() => onModeChange('default')}
              >
                Restore
              </button>
            ) : (
              <button
                type="button"
                className={button}
                aria-label="Expand panel"
                disabled={expandDisabled}
                aria-describedby={expandDisabled ? EXPAND_HINT_ID : undefined}
                onClick={() => onModeChange('expanded')}
              >
                Expand
              </button>
            )}
            <button
              type="button"
              className={button}
              aria-label="Collapse panel"
              onClick={() => onModeChange('collapsed')}
            >
              <Chevron direction="left" />
            </button>
          </>
        )}
        {expandDisabled ? (
          <span id={EXPAND_HINT_ID} className="sr-only">
            Not enough room to expand without shrinking the map.
          </span>
        ) : null}
      </div>
      {nav ? <div className={collapsed ? 'px-1' : 'px-2'}>{nav}</div> : null}
      {/* The body stays mounted when collapsed so its state and focus are preserved. */}
      <div
        hidden={collapsed}
        data-testid="side-panel-scroll"
        onWheel={(e) => e.stopPropagation()}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3"
      >
        <div className="mx-auto max-w-[720px]">{children}</div>
      </div>
    </section>
  );
}
