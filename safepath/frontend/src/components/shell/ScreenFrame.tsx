import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react';

export interface ScreenFrameProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])';

/**
 * Screen shown in the main content area. It is rendered in normal flow below the persistent
 * disclaimer banner and header, so the disclaimer, SIMULATED indicator and 911 guidance stay
 * visible on every screen (Req 1.1, 1.2). On open it moves focus to the heading, Escape closes
 * it, Tab is kept inside, and focus returns to the opener on close (Req 36).
 */
export function ScreenFrame({ title, onClose, children, testId }: ScreenFrameProps) {
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    headingRef.current?.focus();
    return () => {
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
    };
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== 'Tab' || !rootRef.current) return;
    const items = [...rootRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === headingRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      ref={rootRef}
      role="dialog"
      aria-labelledby={headingId}
      data-testid={testId ?? 'screen-frame'}
      onKeyDown={onKeyDown}
      className="relative flex min-h-[60vh] min-w-0 flex-col bg-base pb-24 text-text-primary"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle bg-surface px-3 py-2">
        <h2 id={headingId} ref={headingRef} tabIndex={-1} className="text-lg font-bold focus:outline-2 focus:outline-route">
          {title}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 min-w-11 rounded border border-route px-3 text-sm font-semibold text-route focus:outline-2 focus:outline-route"
        >
          Close<span className="sr-only"> {title}</span>
        </button>
      </div>
      <div className="min-w-0 flex-1 p-3">
        <div className="mx-auto w-full max-w-3xl space-y-4">{children}</div>
      </div>
    </div>
  );
}
