import { useCallback, useEffect, useRef, useState } from 'react';
import {
  demoAdvance,
  demoApplyStep,
  demoListScenarios,
  demoReset,
  demoResetAll,
  demoStart,
  demoStatus,
  type JsonObject,
} from '../services/api';
import type { RealtimeClient } from '../services/realtimeClients';
import { createClient } from '../services/realtimeClients';
import { ActorPanel } from '../components/demo/ActorPanel';
import { ClosureTools } from '../components/demo/ClosureTools';
import { EventLog } from '../components/demo/EventLog';
import { ScenarioCard } from '../components/demo/ScenarioCard';
import { RouteComparison, nextPair, runRoute, type RouteRunPair } from '../components/demo/routeRuns';
import {
  DEMO_ONLY_NOTE,
  STAIRWELL_ID,
  bodiesByActorOf,
  describeError,
  graphLabel,
  parseClientAction,
  parseReset,
  parseScenarios,
  parseStatus,
  resetMessage,
  type BodiesByActor,
  type DemoScenario,
  type DemoStatusInfo,
} from '../components/demo/demoModel';
import { DEMO_ACTORS, secondWindowUrl, startDemoSession, type DemoSessionInfo } from '../components/demo/demoSession';
import { useEventLog } from '../components/demo/useEventLog';
import { fetchIndoorBuilding } from '../components/indoor/indoorService';
import { indoorExit } from '../services/mapData';
import { parseBuilding, type Building, type IndoorGeoJson } from '../components/indoor/indoorModel';
import { SimulatedBadge } from '../components/SimulatedBadge';
import { StatusIcon } from '../components/StatusIcon';

interface Props {
  /** Test seams. */
  makeRealtimeClient?: () => RealtimeClient;
  loadBuilding?: () => Promise<IndoorGeoJson>;
}

type StatusState = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; info: DemoStatusInfo };

const btn =
  'min-h-11 rounded border border-border-subtle bg-surface-raised px-3 py-1 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-route disabled:opacity-60';

/** Demo_Admin_Console (Req 40): SIMULATED scenario control. Hidden unless demo mode is on. */
export default function DemoAdmin({ makeRealtimeClient = createClient, loadBuilding = fetchIndoorBuilding }: Props) {
  const [status, setStatus] = useState<StatusState>({ state: 'loading' });
  const [session, setSession] = useState<DemoSessionInfo | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [sessionVersion, setSessionVersion] = useState(0);
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [globalMessage, setGlobalMessage] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RouteRunPair>>({});
  const [stairBodies, setStairBodies] = useState<BodiesByActor | null>(null);
  const [calcBusy, setCalcBusy] = useState(false);
  const [activeIncident, setActiveIncident] = useState<string | null>(null);
  const buildingRef = useRef<Promise<Building | null> | null>(null);

  const demoOn = status.state === 'ready' && status.info.demoMode;

  // -------------------------------------------------------------- status
  useEffect(() => {
    const ctl = new AbortController();
    demoStatus(ctl.signal).then(
      (b) => setStatus({ state: 'ready', info: parseStatus(b) }),
      (e: unknown) => {
        if (!ctl.signal.aborted) setStatus({ state: 'error', message: describeError(e) });
      },
    );
    return () => ctl.abort();
  }, []);

  // -------------------------------------------------------------- scenarios
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const body = await demoListScenarios(signal);
      if (signal?.aborted) return;
      setScenarios(parseScenarios(body));
      setListError(null);
    } catch (e) {
      if (!signal?.aborted) setListError(describeError(e));
    }
  }, []);

  useEffect(() => {
    if (!demoOn) return;
    const ctl = new AbortController();
    void refresh(ctl.signal);
    return () => ctl.abort();
  }, [demoOn, sessionVersion, refresh]);

  const incidentOf = (id: string) => scenarios.find((s) => s.id === id)?.incidentId || null;
  const realtimeIncident = demoOn ? (activeIncident ?? incidentOf(STAIRWELL_ID) ?? scenarios[0]?.incidentId ?? null) : null;

  // A scenario_reset from any window drops stale route results and refreshes step status.
  const onReset = useCallback(
    (sid: string | null) => {
      setRuns((r) => (sid ? Object.fromEntries(Object.entries(r).filter(([k]) => k !== sid && !k.startsWith(`${sid}:`))) : {}));
      if (sid === null || sid === STAIRWELL_ID) setStairBodies(null);
      void refresh();
    },
    [refresh],
  );
  const { snapshot, log, clearLog } = useEventLog(realtimeIncident, makeRealtimeClient, onReset);

  // -------------------------------------------------------------- session
  const getModerator = async () => {
    setSessionBusy(true);
    setSessionError(null);
    try {
      setSession(await startDemoSession('moderator'));
      setSessionVersion((n) => n + 1);
    } catch (e) {
      setSessionError(describeError(e));
    } finally {
      setSessionBusy(false);
    }
  };

  // -------------------------------------------------------------- route executors
  const getBuilding = (): Promise<Building | null> => {
    buildingRef.current ??= loadBuilding().then(parseBuilding, () => null);
    return buildingRef.current;
  };

  const calcOne = async (key: string, body: JsonObject, indoor: boolean) => {
    let req = body;
    if (indoor && !body.destination) {
      // The demo API's indoor route bodies carry the start only; the destination is the building exit.
      const exit = indoorExit(await loadBuilding().catch(() => null));
      if (exit) req = { ...body, destination: { latitude: exit.lat, longitude: exit.lng } };
    }
    const run = await runRoute(req, indoor ? await getBuilding() : null);
    setRuns((r) => ({ ...r, [key]: nextPair(r[key], run) }));
  };

  const calcStairwell = async (bodies: BodiesByActor) => {
    setCalcBusy(true);
    setGlobalError(null);
    try {
      await Promise.all(Object.entries(bodies).map(([actor, body]) => calcOne(`${STAIRWELL_ID}:${actor}`, body, true)));
    } catch (e) {
      setGlobalError(`Route calculation failed: ${describeError(e)}`);
    } finally {
      setCalcBusy(false);
    }
  };

  const execClientAction = async (sid: string, result: unknown) => {
    const ca = parseClientAction(result);
    if (!ca || ca.do !== 'calculate_routes') return;
    if (sid === STAIRWELL_ID && ca.bodiesByActor) {
      setStairBodies(ca.bodiesByActor);
      await calcStairwell(ca.bodiesByActor);
    } else if (ca.body) {
      await calcOne(sid, ca.body, false);
    }
  };

  // -------------------------------------------------------------- scenario actions
  const act = async (sid: string, fn: () => Promise<Record<string, unknown>>, done: (r: Record<string, unknown>) => string) => {
    setBusyId(sid);
    setErrors((e) => ({ ...e, [sid]: '' }));
    try {
      const res = await fn();
      setMessages((m) => ({ ...m, [sid]: done(res) }));
      const inc = typeof res.incident_id === 'string' ? res.incident_id : incidentOf(sid);
      if (inc) setActiveIncident(inc);
      await refresh();
      try {
        await execClientAction(sid, res);
      } catch (e) {
        setErrors((er) => ({ ...er, [sid]: `Route calculation failed: ${describeError(e)}` }));
      }
    } catch (e) {
      setErrors((er) => ({ ...er, [sid]: describeError(e) }));
    } finally {
      setBusyId(null);
    }
  };

  const stepText = (r: Record<string, unknown>) => {
    if (r.done === true) return 'All steps are applied. Use Reset scenario to start over.';
    const ca = parseClientAction(r);
    const id = typeof r.step_id === 'string' ? `Step ${r.step_id}` : 'Step';
    return `${id} applied${ca ? `; client action: ${ca.do.replace(/_/g, ' ')}` : ''}. ${DEMO_ONLY_NOTE}`;
  };

  const doStart = (s: DemoScenario) => act(s.id, () => demoStart(s.id), () => `Started ${s.name}. ${DEMO_ONLY_NOTE}`);
  const doNext = (s: DemoScenario) => act(s.id, () => demoAdvance(s.id), stepText);
  const doApply = (s: DemoScenario, stepId: string) => act(s.id, () => demoApplyStep(s.id, stepId), stepText);

  const doReset = async (s: DemoScenario) => {
    setBusyId(s.id);
    setErrors((e) => ({ ...e, [s.id]: '' }));
    try {
      const res = parseReset(await demoReset(s.id));
      setMessages((m) => ({ ...m, [s.id]: resetMessage(res) }));
      onReset(s.id);
    } catch (e) {
      setErrors((er) => ({ ...er, [s.id]: describeError(e) }));
    } finally {
      setBusyId(null);
    }
  };

  const doResetAll = async () => {
    setBusyId('*');
    setGlobalError(null);
    try {
      const res = parseReset(await demoResetAll());
      setGlobalMessage(resetMessage(res, 'All scenarios are'));
      onReset(null);
      setMessages({});
    } catch (e) {
      setGlobalError(describeError(e));
    } finally {
      setBusyId(null);
    }
  };

  const graphUsed = status.state === 'ready' ? status.info.graphUsed : null;
  const stairScenario = scenarios.find((s) => s.id === STAIRWELL_ID);

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-3">
      <section
        aria-label="Simulation notice"
        data-testid="demo-banner"
        className="flex flex-wrap items-center gap-2 rounded border-2 border-route-alt p-2 text-sm font-semibold"
      >
        <SimulatedBadge />
        <span>SIMULATED demo console. {DEMO_ONLY_NOTE}</span>
      </section>

      <h2 className="text-xl font-semibold">Demo admin console</h2>

      {status.state === 'loading' && <p role="status">Checking demo mode...</p>}
      {status.state === 'error' && (
        <p role="alert" className="rounded border border-closure p-2 text-sm">
          <StatusIcon kind="cross" /> Demo status unavailable: {status.message}. Demo controls stay hidden.
        </p>
      )}
      {status.state === 'ready' && !status.info.demoMode && (
        <p role="status" data-testid="demo-off" className="rounded border border-border-subtle p-2 text-sm">
          <StatusIcon kind="warning" /> Demo mode is off. The demo controls are disabled and hidden.
        </p>
      )}

      {demoOn && (
        <>
          <section aria-labelledby="session-h" className="space-y-2 rounded border border-border-subtle p-3" data-testid="session-section">
            <h3 id="session-h" className="text-lg font-semibold">Session</h3>
            <p className="text-sm" data-testid="session-identity">
              {session
                ? `You are ${session.subject} with role ${session.role} (SIMULATED demo session).`
                : 'No moderator demo session in this window yet. Admin actions need one.'}
            </p>
            <button type="button" className={btn} onClick={getModerator} disabled={sessionBusy}>
              Get a moderator demo session
            </button>
            {status.state === 'ready' && !status.info.moderatorSessionsAvailable && (
              <p className="text-sm"><StatusIcon kind="warning" /> The server reports that moderator demo sessions are not available.</p>
            )}
            {sessionError && (
              <p role="alert" className="rounded border border-closure p-2 text-sm"><StatusIcon kind="cross" /> {sessionError}</p>
            )}
            <div className="space-y-1 text-sm" data-testid="two-window">
              <h4 className="font-semibold">Two-window demo</h4>
              <p>
                Open the app in a second browser window as a different SIMULATED demo user. That window gets its own
                session, so the realtime events from your actions here appear in both windows.
              </p>
              <ul className="flex flex-wrap gap-2">
                {DEMO_ACTORS.map((a) => (
                  <li key={a}>
                    <a
                      href={secondWindowUrl(a)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex min-h-11 items-center rounded border border-border-subtle px-3 underline"
                    >
                      Open window as demo user {a}
                    </a>
                  </li>
                ))}
              </ul>
              <ol className="list-decimal space-y-0.5 pl-5">
                <li>Get a moderator demo session here.</li>
                <li>Open the second window as user B and keep both visible.</li>
                <li>Start Blocked stairwell, then use the actor buttons below and watch the event log in both windows.</li>
              </ol>
            </div>
          </section>

          <section aria-labelledby="graph-h" className="text-sm" data-testid="graph-used">
            <h3 id="graph-h" className="sr-only">Routing graph in use</h3>
            <StatusIcon kind={graphUsed === 'real' ? 'check' : 'flask'} /> {graphLabel(graphUsed)}
          </section>

          <section aria-labelledby="scen-h" className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 id="scen-h" className="text-lg font-semibold">Scenarios</h3>
              <button type="button" className={btn} onClick={doResetAll} disabled={busyId !== null}>
                Reset all
              </button>
            </div>
            {globalMessage && <p role="status" className="text-sm" data-testid="reset-all-message"><StatusIcon kind="check" /> {globalMessage}</p>}
            {globalError && <p role="alert" className="rounded border border-closure p-2 text-sm"><StatusIcon kind="cross" /> {globalError}</p>}
            {listError && (
              <p role="alert" className="rounded border border-closure p-2 text-sm" data-testid="list-error">
                <StatusIcon kind="cross" /> Scenario list unavailable: {listError}
              </p>
            )}
            {scenarios.map((s) => {
              const keys = Object.keys(runs).filter((k) => k === s.id || k.startsWith(`${s.id}:`));
              return (
                <ScenarioCard
                  key={s.id}
                  scenario={s}
                  busy={busyId === s.id || busyId === '*'}
                  message={messages[s.id] || null}
                  error={errors[s.id] || null}
                  onStart={() => void doStart(s)}
                  onNext={() => void doNext(s)}
                  onApply={(id) => void doApply(s, id)}
                  onReset={() => void doReset(s)}
                >
                  {keys.map((k) => (
                    <RouteComparison
                      key={k}
                      title={k.includes(':') ? `Route for user ${k.split(':')[1]}` : 'Route'}
                      pair={runs[k]}
                    />
                  ))}
                </ScenarioCard>
              );
            })}
          </section>

          {stairScenario && (
            <ActorPanel
              incidentId={stairScenario.incidentId}
              log={log}
              snapshot={snapshot}
              canCalcRoutes={stairBodies !== null}
              calcBusy={calcBusy}
              onCalcRoutes={() => stairBodies && void calcStairwell(stairBodies)}
              onActed={(action, body) => {
                setActiveIncident(stairScenario.incidentId);
                const bodies = bodiesByActorOf(body);
                if (bodies) setStairBodies(bodies);
                // First known bodies give the "before" baseline; a moderator action gives the "after".
                if (bodies && (stairBodies === null || action === 'confirm' || action === 'resolve')) void calcStairwell(bodies);
                void refresh();
              }}
            />
          )}

          <ClosureTools incidentIds={scenarios.map((s) => s.incidentId).filter(Boolean)} />

          <EventLog entries={log} status={snapshot.status} incidentId={realtimeIncident} onClear={clearLog} />
        </>
      )}
    </div>
  );
}
