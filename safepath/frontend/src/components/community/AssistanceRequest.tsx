import { useId, useState, type FormEvent } from 'react';
import { useQueuedSender } from '../../hooks/useOffline';
import { isQueuedResult } from '../../services/offline';
import { postMessage, type JsonObject } from '../../services/api';
import { NoDispatchNotice } from './NoDispatchNotice';
import { MAX_MESSAGE, toFailure } from './types';

export interface AssistanceRequestProps {
  incidentId: string;
  sendMessage?: (body: JsonObject) => Promise<unknown>;
  onSent?: (result: unknown) => void;
}

/** Asks nearby SafePath users for help. Always shows that SafePath does not dispatch emergency services. */
export function AssistanceRequest({ incidentId, sendMessage, onSent }: AssistanceRequestProps) {
  const uid = useId();
  const queuedSend = useQueuedSender('message', postMessage);
  const send = sendMessage ?? queuedSend;
  const [queued, setQueued] = useState(false);
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    const text = content.trim();
    if (!text || content.length > MAX_MESSAGE) {
      setError(!text ? 'Describe what help you need.' : `Message must be at most ${MAX_MESSAGE} characters.`);
      return;
    }
    setError(null);
    setQueued(false);
    setBusy(true);
    try {
      const res = await send({ incident_id: incidentId, message_type: 'assistance_request', content: text });
      setContent('');
      if (isQueuedResult(res)) setQueued(true);
      else {
        setSent(true);
        onSent?.(res);
      }
    } catch (e) {
      setError(toFailure(e).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 text-sm text-text-primary">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${uid}-panel`}
        onClick={() => setOpen((o) => !o)}
        className="rounded border-2 border-unverified px-3 py-1.5 font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
      >
        Request assistance
      </button>
      {open && (
        <form id={`${uid}-panel`} onSubmit={(e) => void submit(e)} noValidate className="space-y-2 rounded border border-border-subtle bg-surface p-2">
          <NoDispatchNotice />
          <label htmlFor={`${uid}-text`} className="block font-medium">What help do you need?</label>
          <textarea
            id={`${uid}-text`}
            rows={3}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full rounded border border-border-subtle bg-base px-2 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
          />
          <div aria-live="polite">
            {error && <p role="alert" className="text-xs text-closure">{error}</p>}
            {queued && !error && <p role="status" className="text-xs text-unverified">Saved on this device and not sent. No one has received this request yet. Call 911 yourself if you are in danger.</p>}
            {sent && !error && <p className="text-xs text-verified">Request posted to the community channel.</p>}
          </div>
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-route px-4 py-2 font-semibold text-base focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary disabled:opacity-60"
          >
            {busy ? 'Sending\u2026' : 'Send request'}
          </button>
        </form>
      )}
    </div>
  );
}
