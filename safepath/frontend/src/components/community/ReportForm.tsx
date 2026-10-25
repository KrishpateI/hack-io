import { useId, useRef, useState, type FormEvent } from 'react';
import { apiBaseUrl, createReport, postJson, type JsonObject } from '../../services/api';
import { useQueuedReportHandler } from '../../hooks/useOffline';
import { getOfflineManager } from '../../services/offline';
import { MAX_DESCRIPTION, REPORT_CATEGORIES, toFailure } from './types';

export interface ReportLocation {
  latitude: number;
  longitude: number;
}

export interface PhotoUploader {
  (file: File): Promise<string>;
}

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const PHOTO_TYPES = ['image/jpeg', 'image/png'];

/** Presign flow: POST /api/photos/presign, then PUT the file to the returned URL. Returns the photo key. */
export const uploadPhotoViaPresign: PhotoUploader = async (file) => {
  const pre = await postJson<{ key: string; method?: string; upload_url: string; headers?: Record<string, string> }>(
    '/api/photos/presign',
    { content_type: file.type, size: file.size },
  );
  const url = pre.upload_url.startsWith('/') ? `${apiBaseUrl()}${pre.upload_url}` : pre.upload_url;
  const res = await fetch(url, { method: pre.method ?? 'PUT', headers: pre.headers ?? { 'Content-Type': file.type }, body: file });
  if (!res.ok) throw new Error(`Photo upload failed (${res.status})`);
  return pre.key;
};

export interface ReportFormProps {
  incidentId: string;
  /** Location chosen by clicking the map. */
  location?: ReportLocation | null;
  /** Indoor feature (stairwell or exit) chosen instead of a map point. */
  indoorFeatureId?: string | null;
  onClearLocation?: () => void;
  onCreated?: (report: unknown) => void;
  submitReport?: (body: JsonObject) => Promise<unknown>;
  uploadPhoto?: PhotoUploader;
  /**
   * Called with the body when the device is offline. Defaults to the Offline_Manager queue.
   * Return `{ ok: false, message }` when the item could not be queued (for example, queue full).
   */
  onOffline?: (body: JsonObject) => void | { ok: boolean; message?: string };
}

const FIELD =
  'min-h-11 w-full rounded-[var(--radius-control)] border border-border-subtle bg-base px-3 py-2 text-base text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-route';

export function ReportForm({
  incidentId,
  location,
  indoorFeatureId,
  onClearLocation,
  onCreated,
  submitReport = createReport,
  uploadPhoto = uploadPhotoViaPresign,
  onOffline: onOfflineProp,
}: ReportFormProps) {
  const uid = useId();
  const queueReport = useQueuedReportHandler();
  const onOffline = onOfflineProp ?? queueReport;
  const [category, setCategory] = useState('');
  const [description, setDescription] = useState('');
  const [photo, setPhoto] = useState<File | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'uploading' | 'submitting' | 'done' | 'queued'>('idle');
  const clientId = useRef<string>(crypto.randomUUID());

  const hasLocation = !!location || !!indoorFeatureId;
  const busy = status === 'uploading' || status === 'submitting';

  function validate(): Record<string, string> {
    const e: Record<string, string> = {};
    if (!category) e.category = 'Choose a category.';
    if (description.length > MAX_DESCRIPTION) e.description = `Description must be at most ${MAX_DESCRIPTION} characters.`;
    if (!hasLocation) e.latitude = 'Click the map or choose an indoor feature to set the location.';
    if (photo && !PHOTO_TYPES.includes(photo.type)) e.photo_key = 'Photo must be a JPEG or PNG image.';
    else if (photo && photo.size > MAX_PHOTO_BYTES) e.photo_key = 'Photo must be 10 MB or smaller.';
    return e;
  }

  function queueOffline(body: JsonObject) {
    const r = onOffline(body);
    if (r && r.ok === false) {
      setStatus('idle');
      setSummary(r.message ?? 'The report could not be saved on this device.');
      return;
    }
    setStatus('queued');
    setCategory('');
    setDescription('');
    setPhoto(null);
    clientId.current = crypto.randomUUID();
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    setSummary(null);
    setStatus('idle');
    const local = validate();
    setErrors(local);
    if (Object.keys(local).length) {
      setSummary('Fix the highlighted fields and try again.');
      return;
    }
    const body: JsonObject = {
      incident_id: incidentId,
      category,
      description: description.trim(),
      client_report_id: clientId.current,
      ...(location ? { latitude: location.latitude, longitude: location.longitude } : {}),
      ...(indoorFeatureId ? { indoor_feature_id: indoorFeatureId } : {}),
    };
    const offlineNow = (typeof navigator !== 'undefined' && navigator.onLine === false) || getOfflineManager().isOffline();
    if (photo && offlineNow && onOfflineProp === undefined) {
      setSummary('You are offline. Photos cannot be saved on this device. Remove the photo to save the report for later.');
      return;
    }
    if (!photo && offlineNow) {
      queueOffline(body);
      return;
    }
    let uploading = false;
    try {
      if (photo) {
        setStatus('uploading');
        uploading = true;
        body.photo_key = await uploadPhoto(photo);
        uploading = false;
      }
      setStatus('submitting');
      const created = await submitReport(body);
      setStatus('done');
      setCategory('');
      setDescription('');
      setPhoto(null);
      clientId.current = crypto.randomUUID();
      onCreated?.(created);
    } catch (err) {
      const f = toFailure(err);
      if (f.network && !uploading) {
        getOfflineManager().noteNetworkFailure();
        queueOffline(body);
        return;
      }
      setStatus('idle');
      setErrors(f.fields);
      setSummary(uploading ? `Photo upload failed. ${f.message}` : f.message);
    }
  }

  const err = (k: string) => errors[k];
  const count = description.length;
  return (
    <form onSubmit={(e) => void onSubmit(e)} noValidate aria-labelledby={`${uid}-h`} className="flex min-h-[70vh] flex-col gap-4 rounded-[var(--radius-card)] bg-surface p-4 text-sm text-text-primary">
      <h2 id={`${uid}-h`} className="text-base font-semibold">Report a hazard</h2>

      <div>
        <label htmlFor={`${uid}-cat`} className="mb-1 block font-medium">Category</label>
        <select
          id={`${uid}-cat`}
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-invalid={!!err('category')}
          aria-describedby={err('category') ? `${uid}-cat-err` : undefined}
          className={FIELD}
        >
          <option value="">Select a category</option>
          {REPORT_CATEGORIES.map((c) => (<option key={c} value={c}>{c}</option>))}
        </select>
        {err('category') && <p id={`${uid}-cat-err`} className="mt-1 text-xs text-closure">{err('category')}</p>}
      </div>

      <div>
        <label htmlFor={`${uid}-desc`} className="mb-1 block font-medium">Description (optional)</label>
        <textarea
          id={`${uid}-desc`}
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          aria-invalid={!!err('description')}
          aria-describedby={`${uid}-count${err('description') ? ` ${uid}-desc-err` : ''}`}
          className={FIELD}
        />
        <p id={`${uid}-count`} className={`mt-1 text-xs ${count > MAX_DESCRIPTION ? 'text-closure' : 'text-text-muted'}`}>
          {count}/{MAX_DESCRIPTION} characters
        </p>
        {err('description') && <p id={`${uid}-desc-err`} className="text-xs text-closure">{err('description')}</p>}
      </div>

      <fieldset className="rounded-[var(--radius-control)] border border-border-subtle p-3">
        <legend className="px-1 font-medium">Location</legend>
        {location ? (
          <p>
            Map point: {location.latitude.toFixed(5)}, {location.longitude.toFixed(5)}{' '}
            {onClearLocation && (
              <button type="button" onClick={onClearLocation} className="ml-1 underline focus:outline-none focus-visible:ring-2 focus-visible:ring-route">
                Clear
              </button>
            )}
          </p>
        ) : indoorFeatureId ? (
          <p>Indoor feature: {indoorFeatureId}</p>
        ) : (
          <p className="text-text-muted">Click the map or choose an indoor stairwell or exit to set the location.</p>
        )}
        {(err('latitude') || err('longitude') || err('indoor_feature_id')) && (
          <p className="mt-1 text-xs text-closure">{err('latitude') ?? err('longitude') ?? err('indoor_feature_id')}</p>
        )}
      </fieldset>

      <div>
        <label htmlFor={`${uid}-photo`} className="mb-1 block font-medium">Photo (optional, JPEG or PNG, up to 10 MB)</label>
        <input
          id={`${uid}-photo`}
          type="file"
          accept="image/jpeg,image/png"
          onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
          aria-invalid={!!err('photo_key')}
          aria-describedby={err('photo_key') ? `${uid}-photo-err` : undefined}
          className="block w-full text-xs text-text-muted"
        />
        {err('photo_key') && <p id={`${uid}-photo-err`} className="mt-1 text-xs text-closure">{err('photo_key')}</p>}
      </div>

      <div aria-live="polite">
        {summary && <p role="alert" className="text-sm text-closure">{summary}</p>}
        {status === 'done' && <p className="text-sm text-verified">Report submitted. It starts as Unverified.</p>}
        {status === 'queued' && (
          <p className="text-sm text-unverified">
            Saved on this device and not sent. Other users and emergency services have not received it. It will be sent when you reconnect.
          </p>
        )}
      </div>

      <button
        type="submit"
        disabled={busy}
        className="mt-auto min-h-11 w-full rounded-[var(--radius-control)] bg-route px-4 py-2 font-semibold text-base focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary disabled:opacity-60"
      >
        {status === 'uploading' ? 'Uploading photo\u2026' : status === 'submitting' ? 'Submitting\u2026' : 'Submit report'}
      </button>
    </form>
  );
}
