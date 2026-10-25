import { useId, useState, type FormEvent } from 'react';
import { useQueuedSender } from '../../hooks/useOffline';
import { isQueuedResult } from '../../services/offline';
import { postMessage, type JsonObject } from '../../services/api';
import { StatusIcon } from '../StatusIcon';
import { NoDispatchNotice } from './NoDispatchNotice';
import { OfficialNotices } from './OfficialNotices';
import {
  ASSISTANCE_MESSAGE_TYPES,
  MAX_MESSAGE,
  MESSAGE_TYPES,
  formatTime,
  toFailure,
  toNotice,
  type CommunityMessage,
  type OfficialNoticeItem,
} from './types';

export interface MessageChannelProps {
  incidentId: string;
  messages: readonly CommunityMessage[];
  /** Official notices from the notices feed (separate from community messages). */
  notices?: readonly OfficialNoticeItem[];
  sendMessage?: (body: JsonObject) => Promise<unknown>;
  onSent?: (result: unknown) => void;
}

const typeLabel = (v: string) => MESSAGE_TYPES.find((t) => t.value === v)?.label ?? v;

export function MessageChannel({ incidentId, messages, notices = [], sendMessage, onSent }: MessageChannelProps) {
  const uid = useId();
  const queuedSend = useQueuedSender('message', postMessage);
  const send = sendMessage ?? queuedSend;
  const [queued, setQueued] = useState(false);
  const [type, setType] = useState<string>('update');
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Anything flagged official goes to the Official notices panel, never the community list.
  const official = [...notices, ...messages.filter((m) => m.is_official).map(toNotice)];
  const community = messages
    .filter((m) => !m.is_official)
    .sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    const text = content.trim();
    const local: Record<string, string> = {};
    if (!text) local.content = 'Enter a message.';
    else if (content.length > MAX_MESSAGE) local.content = `Message must be at most ${MAX_MESSAGE} characters.`;
    setFieldErrors(local);
    setError(null);
    setQueued(false);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      const res = await send({ incident_id: incidentId, message_type: type, content: text });
      setContent('');
      if (isQueuedResult(res)) setQueued(true);
      else onSent?.(res);
    } catch (e) {
      const f = toFailure(e);
      setFieldErrors(f.fields);
      setError(f.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby={`${uid}-h`} className="space-y-3 text-sm text-text-primary">
      <h2 id={`${uid}-h`} className="text-base font-semibold">Messages</h2>
      <OfficialNotices notices={official} />

      <div>
        <h3 className="mb-1 text-sm font-semibold">Community messages</h3>
        {/* role="log" is not allowed on <ul>; the scrollable log wraps a real list (axe aria-allowed-role). */}
        <div role="log" aria-live="polite" aria-relevant="additions" aria-label="Community messages" className="max-h-72 overflow-y-auto" tabIndex={0}>
          <ul className="space-y-2">
            {community.length === 0 && <li className="text-text-muted">No community messages yet.</li>}
            {community.map((m) => (
              <li key={m.message_id} data-testid="community-message" className="space-y-1 rounded-[var(--radius-card)] border border-border-subtle bg-surface p-3 shadow-[var(--shadow-card)]">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="inline-flex items-center gap-1 rounded border border-unverified px-1.5 py-0.5 text-unverified">
                    <StatusIcon kind="people" />
                    <span>Community</span>
                  </span>
                  <span className="text-text-muted">{typeLabel(m.message_type)}</span>
                  <span className="text-text-muted">{m.alias ?? 'Anonymous'}</span>
                  <time dateTime={m.created_at ?? undefined} className="text-text-muted">{formatTime(m.created_at)}</time>
                </div>
                <p className="break-words">{m.content}</p>
                {ASSISTANCE_MESSAGE_TYPES.includes(m.message_type) && <NoDispatchNotice />}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <form onSubmit={(e) => void submit(e)} noValidate className="space-y-2">
        <div>
          <label htmlFor={`${uid}-type`} className="mb-1 block font-medium">Message type</label>
          <select
            id={`${uid}-type`}
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="w-full rounded border border-border-subtle bg-base px-2 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
          >
            {MESSAGE_TYPES.map((t) => (<option key={t.value} value={t.value}>{t.label}</option>))}
          </select>
        </div>
        {ASSISTANCE_MESSAGE_TYPES.includes(type) && <NoDispatchNotice />}
        <div>
          <label htmlFor={`${uid}-text`} className="mb-1 block font-medium">Message</label>
          <textarea
            id={`${uid}-text`}
            rows={3}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            aria-invalid={!!fieldErrors.content}
            aria-describedby={`${uid}-count${fieldErrors.content ? ` ${uid}-err` : ''}`}
            className="w-full rounded border border-border-subtle bg-base px-2 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
          />
          <p id={`${uid}-count`} className={`mt-1 text-xs ${content.length > MAX_MESSAGE ? 'text-closure' : 'text-text-muted'}`}>
            {content.length}/{MAX_MESSAGE} characters
          </p>
          {fieldErrors.content && <p id={`${uid}-err`} className="text-xs text-closure">{fieldErrors.content}</p>}
        </div>
        {error && <p role="alert" className="text-xs text-closure">{error}</p>}
        {queued && !error && <p role="status" className="text-xs text-unverified">Saved on this device and not sent. Other users have not received it. It will be sent when you reconnect.</p>}
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-route px-4 py-2 font-semibold text-base focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary disabled:opacity-60"
        >
          {busy ? 'Sending\u2026' : 'Send message'}
        </button>
      </form>
    </section>
  );
}
