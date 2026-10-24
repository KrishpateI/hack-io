import { useEffect, useState } from 'react';
import { demoAct } from '../../services/api';
import type { RealtimeSnapshot } from '../../services/realtimeController';
import { StatusIcon } from '../StatusIcon';
import {
  OBSERVE_LIMIT_SECONDS,
  STAIRWELL_ID,
  actResultText,
  describeError,
  type ActorAction,
} from './demoModel';
import type { LogEntry } from './useEventLog';

type Actor = 'A' | 'B' | 'C' | 'moderator';

interface ActorButton {
  key: string;
  label: string;
  actor: Actor;
  action: ActorAction;
  message_type?: string;
}

export const ACTOR_BUTTONS: readonly ActorButton[] = [
  { key: 'a-report', label: 'A reports Stairwell A blocked', actor: 'A', action: 'report' },
  { key: 'c-corroborate', label: 'C corroborates the report', actor: 'C', action: 'corroborate' },
  { key: 'm-confirm', label: 'Moderator confirms SIMULATED closure', actor: 'moderator', action: 'confirm' },
  { key: 'b-message', label: 'B sends assistance request', actor: 'B', action: 'message', message_type: 'assistance_request' },
];

interface Props {
  incidentId: string;
  log: LogEntry[];
  snapshot: RealtimeSnapshot;
  /** Called after a successful act so the page can store route bodies / recalculate. */
  onActed: (action: ActorAction, body: Record<string, unknown>) => void;
  /** Route bodies are known: show the manual recalculation button. */
  canCalcRoutes: boolean;
  onCalcRoutes: () => void;
  calcBusy: boolean;
}

/** Actor panel for the blocked stairwell scenario (Req 40, 39, 37): SIMULATED users A, B, C and the moderator. */
export function ActorPanel({ incidentId, log, snapshot, onActed, canCalcRoutes, onCalcRoutes, calcBusy }: Props) {
  const [results, setResults] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [watchFrom, setWatchFrom] = useState<number | null>(null);
  const [now, setNow] = useState(0);

  const arrival = watchFrom === null ? undefined : [...log].reverse().find((e) => e.type.toLowerCase().startsWith('report') && e.receivedAt >= watchFrom);
  const waiting = watchFrom !== null && !arrival;

  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [waiting]);

  const startWatch = () => {
    const t = Date.now();
    setNow(t);
    setWatchFrom(t);
  };

  const run = async (b: ActorButton) => {
    setBusy(b.key);
    setError(null);
    try {
      const body = await demoAct(STAIRWELL_ID, {
        actor: b.actor,
        action: b.action,
        ...(b.message_type ? { message_type: b.message_type } : {}),
      });
      setResults((r) => ({ ...r, [b.key]: actResultText(b.action, body) }));
      if (b.key === 'a-report') startWatch();
      onActed(b.action, body);
    } catch (e) {
      setError(`${b.label}: ${describeError(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const elapsed = watchFrom === null ? 0 : ((arrival ? arrival.receivedAt : now) - watchFrom) / 1000;
  const reports = Object.values(snapshot.store.reports);
  const latest = reports.length ? reports[reports.length - 1] : null;
  const storeStatus = latest
    ? String(latest.status_label ?? latest.verification_status ?? 'status unknown')
    : null;

  return (
    <section aria-labelledby="actor-h" className="space-y-3 rounded border border-border-subtle p-3" data-testid="actor-panel">
      <h3 id="actor-h" className="text-lg font-semibold">Blocked stairwell: actors (SIMULATED)</h3>
      <p className="text-sm text-text-muted">
        Users A, B and C and the moderator are invented demo accounts for incident {incidentId}. Demo only, not real events.
      </p>
      <ol className="space-y-2">
        <li>
          <ActorRow button={ACTOR_BUTTONS[0]} busy={busy} onRun={run} result={results['a-report']} />
        </li>
        <li className="space-y-1">
          <button type="button" className={btn} onClick={startWatch} disabled={busy !== null}>
            B observes (watch for the report)
          </button>
          {watchFrom !== null && (
            <p role="status" data-testid="observe-status" className="text-sm">
              <StatusIcon kind={arrival ? 'check' : 'question'} />{' '}
              {arrival
                ? `Report event received after ${elapsed.toFixed(1)} s (${elapsed <= OBSERVE_LIMIT_SECONDS ? 'within' : 'over'} the ${OBSERVE_LIMIT_SECONDS} s target).`
                : `Waiting for the report event: ${elapsed.toFixed(1)} s elapsed (target ${OBSERVE_LIMIT_SECONDS} s).`}
              {storeStatus ? ` Report status in the realtime store: ${storeStatus}.` : ''}
            </p>
          )}
        </li>
        {ACTOR_BUTTONS.slice(1).map((b) => (
          <li key={b.key}>
            <ActorRow button={b} busy={busy} onRun={run} result={results[b.key]} />
          </li>
        ))}
      </ol>
      <div>
        <button type="button" className={btn} onClick={onCalcRoutes} disabled={!canCalcRoutes || calcBusy}>
          Calculate routes for A, B and C
        </button>
        {!canCalcRoutes && <span className="ml-2 text-xs text-text-muted">Available after the first actor action.</span>}
      </div>
      {error && (
        <p role="alert" className="rounded border border-closure p-2 text-sm">
          <StatusIcon kind="cross" /> {error}
        </p>
      )}
    </section>
  );
}

const btn =
  'min-h-11 rounded border border-border-subtle bg-surface-raised px-3 py-1 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-route disabled:opacity-60';

function ActorRow({
  button,
  busy,
  onRun,
  result,
}: {
  button: ActorButton;
  busy: string | null;
  onRun: (b: ActorButton) => void;
  result?: string;
}) {
  return (
    <div className="space-y-1">
      <button type="button" className={btn} onClick={() => onRun(button)} disabled={busy !== null}>
        {button.label}
      </button>
      {busy === button.key && <span role="status" className="ml-2 text-xs">Working...</span>}
      {result && (
        <p className="text-sm" data-testid={`result-${button.key}`}>
          <StatusIcon kind="check" /> {result}
        </p>
      )}
    </div>
  );
}
