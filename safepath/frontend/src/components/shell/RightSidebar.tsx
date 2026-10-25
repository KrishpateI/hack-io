import { useId, useState } from 'react';
import type { EventStoreState } from '../../services/eventStore';
import { SegmentedControl } from '../ios/SegmentedControl';
import { AssistanceRequest } from '../community/AssistanceRequest';
import { CheckInPanel } from '../community/CheckInPanel';
import { MessageChannel } from '../community/MessageChannel';
import { ReportList } from '../community/ReportList';
import {
  ASSISTANCE_MESSAGE_TYPES,
  formatTime,
  type CommunityCheckIn,
  type CommunityMessage,
  type CommunityReport,
} from '../community/types';
import { useCommunityFeed } from '../community/useCommunityFeed';

export interface CommunityFeed {
  reports: readonly CommunityReport[];
  messages: readonly CommunityMessage[];
  checkins: readonly CommunityCheckIn[];
}

export type RightSidebarTab = 'reports' | 'messages' | 'checkins';

const TABS: readonly { value: RightSidebarTab; label: string }[] = [
  { value: 'reports', label: 'Reports' },
  { value: 'messages', label: 'Messages & notices' },
  { value: 'checkins', label: 'Check-ins & assistance' },
];

export interface RightSidebarViewProps {
  incidentId: string;
  feed: CommunityFeed;
  location?: { latitude: number; longitude: number } | null;
  initialTab?: RightSidebarTab;
  ownAlias?: string | null;
}

/** Presentational right sidebar (Req 34.4). Official notices stay in their own bordered panel. */
export function RightSidebarView({ incidentId, feed, location, initialTab = 'reports', ownAlias }: RightSidebarViewProps) {
  const uid = useId();
  const [tab, setTab] = useState<RightSidebarTab>(initialTab);
  const [open, setOpen] = useState(true);
  const assistance = feed.messages.filter((m) => !m.is_official && ASSISTANCE_MESSAGE_TYPES.includes(m.message_type));
  const officialCount = feed.messages.filter((m) => m.is_official).length;
  const communityCount = feed.messages.length - officialCount;

  return (
    <aside aria-label="Community and official updates" data-testid="right-sidebar" className="w-full bg-surface text-text-primary md:w-96">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${uid}-body`}
        onClick={() => setOpen((o) => !o)}
        className="min-h-[var(--target-min)] w-full border-b border-border-subtle px-4 text-left text-[length:var(--text-body)] font-semibold focus:outline-2 focus:outline-route md:hidden"
      >
        {open ? 'Hide' : 'Show'} reports, messages and check-ins
      </button>
      <div id={`${uid}-body`} hidden={!open} className="md:block!">
        <div role="status" aria-live="polite" data-testid="right-sidebar-live" className="sr-only">
          {feed.reports.length} community reports, {communityCount} community messages, {officialCount} official notices,{' '}
          {feed.checkins.length} check-ins.
        </div>
        <div className="p-3 pb-0">
          <SegmentedControl<RightSidebarTab>
            variant="tabs"
            label="Community and official updates"
            idPrefix={uid}
            options={TABS}
            value={tab}
            onChange={setTab}
          />
        </div>
        <div
          id={`${uid}-panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`${uid}-tab-${tab}`}
          data-testid={`panel-${tab}`}
          className="space-y-3 overflow-y-auto p-3"
        >
          {tab === 'reports' && <ReportList reports={feed.reports} ownAlias={ownAlias} />}
          {tab === 'messages' && (
            <>
              <p className="text-xs text-text-muted">
                Official notices appear in the Official card with a shield icon and carry a source. Everything else here is from other SafePath users.
              </p>
              <MessageChannel incidentId={incidentId} messages={feed.messages} />
            </>
          )}
          {tab === 'checkins' && (
            <>
              <CheckInPanel incidentId={incidentId} location={location} checkins={feed.checkins} />
              <section aria-labelledby={`${uid}-assist-h`} data-testid="assistance-requests" className="space-y-2">
                <h3 id={`${uid}-assist-h`} className="text-[length:var(--text-body)] font-semibold">Assistance requests from the community</h3>
                <p className="text-xs text-text-muted">These are messages from other users. They are not sent to emergency services.</p>
                <div aria-live="polite" aria-relevant="additions" className="space-y-1">
                  {assistance.length === 0 && <p className="text-sm text-text-muted">No assistance requests.</p>}
                  {assistance.map((m) => (
                    <p key={m.message_id} className="break-words rounded-[var(--radius-card)] border border-border-subtle bg-surface p-3 text-sm shadow-[var(--shadow-card)]">
                      <span className="text-xs text-text-muted">{m.alias ?? 'Anonymous'} · {formatTime(m.created_at)}</span>
                      <br />
                      {m.content}
                    </p>
                  ))}
                </div>
              </section>
              <AssistanceRequest incidentId={incidentId} />
            </>
          )}
        </div>
      </div>
    </aside>
  );
}

export interface RightSidebarProps {
  incidentId: string;
  /** Store from `useRealtime(incidentId)`. */
  store: EventStoreState | undefined;
  location?: { latitude: number; longitude: number } | null;
  initialTab?: RightSidebarTab;
  ownAlias?: string | null;
}

/** Right sidebar wired to the REST + realtime community feed. */
export function RightSidebar({ incidentId, store, ...rest }: RightSidebarProps) {
  const feed = useCommunityFeed(incidentId, store);
  return <RightSidebarView incidentId={incidentId} feed={feed} {...rest} />;
}
