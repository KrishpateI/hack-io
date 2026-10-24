import { StatusIcon } from '../StatusIcon';
import type { LogEntry } from './useEventLog';
import type { ConnectionStatus } from '../../services/realtimeController';

const fmt = (ms: number) => new Date(ms).toISOString().slice(11, 23) + ' UTC';

/** Realtime events received by this window: proves two-window sync. SIMULATED demo events only. */
export function EventLog({
  entries,
  status,
  incidentId,
  onClear,
}: {
  entries: LogEntry[];
  status: ConnectionStatus;
  incidentId: string | null;
  onClear: () => void;
}) {
  return (
    <section aria-labelledby="log-h" className="space-y-2 rounded border border-border-subtle p-3" data-testid="event-log">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="log-h" className="text-lg font-semibold">Realtime event log</h3>
        <button type="button" className="min-h-11 rounded border border-border-subtle px-3 text-sm" onClick={onClear}>
          Clear log
        </button>
      </div>
      <p className="text-sm text-text-muted" role="status">
        Connection: {status}
        {incidentId ? ` | incident ${incidentId}` : ' | no incident selected yet'}. Open the app in a second window to see the same events arrive there.
      </p>
      {entries.length === 0 ? (
        <p className="text-sm">No events received yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Realtime events received by this window</caption>
            <thead>
              <tr>
                <th scope="col" className="pr-3">Type</th>
                <th scope="col" className="pr-3">Received</th>
                <th scope="col">Simulated</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.key}>
                  <td className="pr-3 break-all">{e.type}</td>
                  <td className="pr-3 whitespace-nowrap">{fmt(e.receivedAt)}</td>
                  <td>
                    <StatusIcon kind={e.simulated ? 'flask' : 'question'} /> {e.simulated ? 'SIMULATED' : 'not flagged'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
