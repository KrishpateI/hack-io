import { Card } from '../ios/Card';
import { TabIcon } from '../ios/icons';
import { SimulatedBadge } from '../SimulatedBadge';
import { StatusIcon } from '../StatusIcon';
import { formatTime, type OfficialNoticeItem } from './types';

/**
 * Official notices, kept apart from community messages. The Official label and source record
 * appear only when `is_official` is true (Req 26.4, 28).
 */
export function OfficialNotices({ notices }: { notices: readonly OfficialNoticeItem[] }) {
  return (
    <Card tone="success" className="bg-surface-raised">
    <section aria-labelledby="official-notices-heading" data-testid="official-notices" className="space-y-2">
      <h3 id="official-notices-heading" className="flex items-center gap-1.5 text-[length:var(--text-body)] font-semibold text-text-primary">
        <span aria-hidden="true" className="flex shrink-0 items-center text-verified">
          <TabIcon name="safety" size={18} />
        </span>
        Official notices
      </h3>
      <div aria-live="polite" aria-relevant="additions" className="space-y-2">
        {notices.length === 0 && <p className="text-sm text-text-muted">No official notices.</p>}
        {notices.map((n) => (
          <article key={n.notice_id} data-testid="official-notice" className="space-y-1 text-sm text-text-primary">
            <div className="flex flex-wrap items-center gap-1.5">
              {n.is_official && (
                <span className="inline-flex items-center gap-1 rounded border border-verified px-1.5 py-0.5 text-xs text-verified">
                  <StatusIcon kind="check" />
                  <span>Official</span>
                </span>
              )}
              {n.simulated && <SimulatedBadge />}
              <time dateTime={n.published_at ?? undefined} className="text-xs text-text-muted">{formatTime(n.published_at)}</time>
            </div>
            <p className="break-words">{n.text}</p>
            {n.is_official && (n.source_organization || n.source_record_id || n.source_url) && (
              <p className="break-words text-xs text-text-muted">
                Source: {n.source_organization ?? 'Unknown'}
                {n.source_record_id ? ` (record ${n.source_record_id})` : ''}
                {n.source_url && (
                  <>
                    {' '}
                    <a href={n.source_url} target="_blank" rel="noreferrer noopener" className="underline focus:outline-none focus-visible:ring-2 focus-visible:ring-route">
                      View source
                    </a>
                  </>
                )}
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
    </Card>
  );
}
