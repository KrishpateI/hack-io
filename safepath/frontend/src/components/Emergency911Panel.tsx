import { useId, useRef, useState } from 'react';
import { useDismissibleLayer } from './ios/hooks/useDismissibleLayer';
import { Emergency911Guidance } from './Emergency911Guidance';

/**
 * Persistent 911 control, rendered inside the Header (outside `main`) so it is one
 * interaction away from every screen. The popover is a `section` and closes with
 * Escape, returning focus to the button. Guidance only: SafePath does not dispatch
 * or notify 911 (Req 3.4, 10.3, 10.6).
 */
export function Emergency911Panel() {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const popoverId = useId();
  useDismissibleLayer(open, () => setOpen(false), button);
  return (
    <div data-testid="emergency-911" className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="min-h-[var(--target-min)] rounded-full bg-closure px-3 py-1.5 text-sm font-bold text-base"
      >
        <span className="inline-flex items-center justify-center gap-1.5">
          <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25c1.1.37 2.3.57 3.6.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.6 21 3 13.4 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.6a1 1 0 0 1-.25 1z" />
          </svg>
          <span>Emergency: call 911</span>
        </span>
      </button>
      {open && (
        <section
          id={popoverId}
          aria-label="Emergency 911 guidance"
          className="absolute right-0 top-full z-50 mt-2 w-[min(20rem,calc(100vw-1.5rem))] rounded-[var(--radius-card)] border-2 border-closure bg-surface p-3 shadow-[var(--shadow-card)]"
        >
          <div data-testid="emergency-911-body">
            <Emergency911Guidance />
          </div>
        </section>
      )}
    </div>
  );
}
