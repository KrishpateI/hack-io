import { HistoricalLabel } from './HistoricalLabel';
import { SourceLabel } from './SourceLabel';
import { StatusBadge, type Status } from './StatusBadge';
import { labelAttribute } from '../services/labels';
import type { SourceRecord } from '../services/sources';

function fmt(value: string | null | undefined): string {
  if (!value) return 'Unknown';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
}

const BADGE: Partial<Record<string, Status>> = { verified: 'verified', unverified: 'unverified', unknown: 'unknown' };

/** Source details for a selected feature; renders synchronously from the record (Req 10.3, 10.6). */
export function SourceRecordPopup({ record, onClose }: { record: SourceRecord | null | undefined; onClose?: () => void }) {
  if (!record) {
    return (
      <section data-testid="source-popup" aria-label="Source details" className="rounded border border-border-subtle bg-surface-raised p-3">
        <p className="text-sm">Source: Unknown source. No Source_Record is attached to this feature.</p>
      </section>
    );
  }
  const rows: [string, React.ReactNode][] = [
    ['Organization', labelAttribute(record.organization)],
    [
      'URL',
      <a key="u" href={record.url} target="_blank" rel="noopener noreferrer" className="break-all text-route underline">
        {record.url}
      </a>,
    ],
    ['Title', labelAttribute(record.title)],
    ['Publication / update date', fmt(record.published_at)],
    ['Retrieved', fmt(record.retrieved_at)],
    ['Classification', labelAttribute(record.data_classification)],
    [
      'Verification status',
      <span key="v" className="inline-flex items-center gap-2">
        {BADGE[record.verification_status] ? <StatusBadge status={BADGE[record.verification_status]} /> : null}
        <span>{labelAttribute(record.verification_status)}</span>
      </span>,
    ],
    ['Page / section', labelAttribute(record.page_or_section)],
  ];
  return (
    <section data-testid="source-popup" aria-label="Source details" className="rounded border border-border-subtle bg-surface-raised p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <SourceLabel item={{ source_record: { is_official: record.is_official_source, source_id: record.source_id } }} />
        {record.data_classification === 'Historical' ? <HistoricalLabel /> : null}
        {onClose ? (
          <button type="button" onClick={onClose} className="ml-auto rounded border border-border-subtle px-2 py-0.5">
            Close
          </button>
        ) : null}
      </div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-text-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
