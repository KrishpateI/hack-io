import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import type { MeInfo } from '../../services/api';

export interface ProfileMenuProps {
  me: MeInfo | null;
  loading?: boolean;
  error?: string | null;
}

export function accountKind(me: MeInfo | null): string {
  if (!me) return 'Unknown';
  return me.is_guest ? 'Guest' : 'Registered user';
}

/** Profile control: role, display alias, guest or user, and session info (Req 22, 34.1). */
export function ProfileMenu({ me, loading = false, error = null }: ProfileMenuProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const button = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && open) {
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
    }
  };

  const alias = me?.display_name?.trim() || 'Unknown';
  return (
    <div ref={root} onKeyDown={onKeyDown} className="relative" data-testid="profile-menu">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex min-h-11 items-center gap-2 rounded border border-border-subtle px-3 text-sm text-text-primary"
      >
        <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7H4Z" />
        </svg>
        <span>Profile{me?.display_name?.trim() ? `: ${alias}` : ''}</span>
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-label="Profile and session"
          data-testid="profile-panel"
          className="absolute right-0 z-30 mt-1 w-64 max-w-[calc(100vw-1.5rem)] rounded border border-border-subtle bg-surface-raised p-3 text-sm shadow-lg"
        >
          {loading && <p role="status">Loading profile…</p>}
          {error && <p role="alert" className="text-closure">Could not load the profile: {error}</p>}
          {me && (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
              <dt className="text-text-muted">Account</dt>
              <dd>{accountKind(me)}</dd>
              <dt className="text-text-muted">Role</dt>
              <dd>{me.role}</dd>
              <dt className="text-text-muted">Display alias</dt>
              <dd className="break-words">{alias}</dd>
              <dt className="text-text-muted">Session expires</dt>
              <dd>{me.expires_at ? new Date(me.expires_at).toLocaleString() : 'Unknown'}</dd>
            </dl>
          )}
          <p className="mt-2 text-xs text-text-muted">Other people see only your alias, never your account details.</p>
        </div>
      )}
    </div>
  );
}
