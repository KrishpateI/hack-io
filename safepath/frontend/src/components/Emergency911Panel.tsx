import { useState } from 'react';

/**
 * Persistent 911 guidance, visually separate from app content. One click from
 * any screen (rendered in RootLayout). Guidance only: SafePath does not
 * dispatch or notify 911 (Req 1.3, 1.4).
 */
export function Emergency911Panel() {
  const [open, setOpen] = useState(false);
  return (
    <aside
      aria-label="Emergency 911 guidance"
      data-testid="emergency-911"
      className="fixed bottom-3 right-3 z-50 max-w-xs rounded-lg border-2 border-closure bg-base p-2 text-sm text-text-primary shadow-lg"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="w-full rounded bg-closure px-3 py-1.5 font-bold text-base"
      >
        <span className="inline-flex items-center justify-center gap-1.5">
          <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25c1.1.37 2.3.57 3.6.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.6 21 3 13.4 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.6a1 1 0 0 1-.25 1z" />
          </svg>
          <span>Emergency: call 911</span>
        </span>
      </button>
      {open && (
        <div data-testid="emergency-911-body" className="mt-2 space-y-1">
          <p>If you are in immediate danger, call 911 from your phone now.</p>
          <p className="text-text-muted">
            SafePath does not call, dispatch, or notify 911 or any emergency service. You must place the call yourself.
          </p>
        </div>
      )}
    </aside>
  );
}
