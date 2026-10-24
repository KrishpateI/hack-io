import { useId, useState, type FormEvent } from 'react';
import { useQueuedSender } from '../../hooks/useOffline';
import { isQueuedResult } from '../../services/offline';
import { postCheckin, type JsonObject } from '../../services/api';
import { NoDispatchNotice } from './NoDispatchNotice';
import { CHECKIN_STATUSES, formatTime, toFailure, type CommunityCheckIn } from './types';

export const LOCATION_EXPLANATION =
  'If you turn this on, only an approximate location (rounded to about 200 m) is shared, never your exact position.';
export const RETENTION_NOTE =
  'Shared locations are removed automatically after 72 hours. Your check-in status is kept for the incident.';

export interface CheckInPanelProps {
  incidentId: string;
  /** Current device position; only used when the user opts in to sharing. */
  location?: { latitude: number; longitude: number } | null;
  /** Other users' visible check-ins (live from the realtime store). */
  checkins?: readonly CommunityCheckIn[];
  submitCheckin?: (body: JsonObject) => Promise<unknown>;
  onSubmitted?: (result: unknown) => void;
}

const statusLabel = (c: CommunityCheckIn) =>
  c.status_label ?? CHECKIN_STATUSES.find((s) => s.value === c.status)?.label ?? c.status;

export function CheckInPanel({ incidentId, location, checkins = [], submitCheckin, onSubmitted }: CheckInPanelProps) {
  const uid = useId();
  const queuedSend = useQueuedSender('checkin', postCheckin);
  const send = submitCheckin ?? queuedSend;
  const [queued, setQueued] = useState(false);
  const [status, setStatus] = useState<string>('');
  const [shareLocation, setShareLocation] = useState(false); // opt-in, off by default (Req 27.3)
  const [visibility, setVisibility] = useState('private');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<string | null>(null);

  const noLocation = shareLocation && !location;

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setError(null);
    setSubmitted(null);
    setQueued(false);
    if (!status) {
      setFieldError('Choose a status.');
      return;
    }
    if (noLocation) {
      setFieldError('Your location is not available. Turn off location sharing or enable location access.');
      return;
    }
    setFieldError(null);
    setBusy(true);
    try {
      const body: JsonObject = { incident_id: incidentId, status, visibility, share_location: shareLocation };
      if (shareLocation && location) body.location = { latitude: location.latitude, longitude: location.longitude };
      const res = await send(body);
      if (isQueuedResult(res)) setQueued(true);
      else {
        setSubmitted(status);
        onSubmitted?.(res);
      }
    } catch (e) {
      const f = toFailure(e);
      setFieldError(f.fields.status ?? f.fields.location ?? f.fields.visibility ?? null);
      setError(f.message);
    } finally {
      setBusy(false);
    }
  }

  const showNotice = status === 'need_assistance' || submitted === 'need_assistance';
  return (
    <section aria-labelledby={`${uid}-h`} className="space-y-3 text-sm text-text-primary">
      <h2 id={`${uid}-h`} className="text-base font-semibold">Safety check-in</h2>
      <form onSubmit={(e) => void submit(e)} noValidate className="space-y-3">
        <fieldset aria-describedby={fieldError ? `${uid}-err` : undefined}>
          <legend className="mb-1 font-medium">Your status</legend>
          <div className="space-y-1">
            {CHECKIN_STATUSES.map((s) => (
              <label key={s.value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name={`${uid}-status`}
                  value={s.value}
                  checked={status === s.value}
                  onChange={() => setStatus(s.value)}
                  className="focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
                />
                <span>{s.label}</span>
              </label>
            ))}
          </div>
          {fieldError && <p id={`${uid}-err`} className="mt-1 text-xs text-closure">{fieldError}</p>}
        </fieldset>

        {showNotice && <NoDispatchNotice />}

        <div className="space-y-1 rounded border border-border-subtle p-2">
          <label className="flex items-center gap-2 font-medium">
            <input
              type="checkbox"
              role="switch"
              checked={shareLocation}
              onChange={(e) => setShareLocation(e.target.checked)}
              aria-describedby={`${uid}-loc`}
              className="focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
            />
            <span>Share my approximate location</span>
          </label>
          <p id={`${uid}-loc`} className="text-xs text-text-muted">
            {LOCATION_EXPLANATION} Location sharing is off unless you turn it on.
          </p>
        </div>

        <div>
          <label htmlFor={`${uid}-vis`} className="mb-1 block font-medium">Who can see this check-in</label>
          <select
            id={`${uid}-vis`}
            value={visibility}
            onChange={(e) => setVisibility(e.target.value)}
            className="w-full rounded border border-border-subtle bg-base px-2 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-route"
          >
            <option value="private">Only me</option>
            <option value="incident-members">Signed-in users in this incident</option>
            <option value="public">Everyone in this incident</option>
          </select>
        </div>

        <p className="text-xs text-text-muted">{RETENTION_NOTE}</p>

        <div aria-live="polite">
          {error && <p role="alert" className="text-xs text-closure">{error}</p>}
          {queued && !error && <p role="status" className="text-xs text-unverified">Saved on this device and not sent. Your check-in has not been received yet. It will be sent when you reconnect.</p>}
          {submitted && !error && <p className="text-xs text-verified">Check-in recorded.</p>}
        </div>
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-route px-4 py-2 font-semibold text-base focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary disabled:opacity-60"
        >
          {busy ? 'Sending\u2026' : 'Check in'}
        </button>
      </form>

      <div>
        <h3 className="mb-1 text-sm font-semibold">Recent check-ins</h3>
        <ul aria-live="polite" aria-relevant="additions" className="space-y-1">
          {checkins.length === 0 && <li className="text-text-muted">No visible check-ins yet.</li>}
          {checkins.map((c) => (
            <li key={c.checkin_id} className="rounded border border-border-subtle bg-surface p-2 text-xs">
              <span className="font-medium">{statusLabel(c)}</span>{' '}
              <span className="text-text-muted">{c.alias ?? 'Anonymous'}{' \u00b7 '}{formatTime(c.created_at)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
