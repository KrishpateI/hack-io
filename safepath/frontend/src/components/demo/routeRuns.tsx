import { calculateRoute, type JsonObject, type RouteResponse } from '../../services/api';
import { explainChange } from '../../services/routeRecalc';
import { buildRoute, type Building } from '../indoor/indoorModel';
import { StatusIcon } from '../StatusIcon';
import { summarizeRoute, type RouteSummary } from './demoModel';

export interface RouteRun {
  summary: RouteSummary;
  /** Indoor step text (stairwell scenario), or null when not indoor / no route. */
  steps: string[] | null;
  response: RouteResponse;
}
export interface RouteRunPair {
  before: RouteRun | null;
  after: RouteRun;
  change: string | null;
}

/** Calculate one route and summarise it (indoor steps via the indoor model's buildRoute). */
export async function runRoute(
  body: JsonObject,
  building: Building | null,
  calc: (b: JsonObject) => Promise<RouteResponse> = calculateRoute,
): Promise<RouteRun> {
  const response = await calc(body);
  const summary = summarizeRoute(response);
  const steps = building && summary.edgeIds.length ? (buildRoute(building, summary.edgeIds)?.steps ?? null) : null;
  return { summary, steps, response };
}

/** The previous "after" becomes the new "before"; explain what changed with explainChange. */
export function nextPair(prev: RouteRunPair | undefined, run: RouteRun): RouteRunPair {
  const before = prev?.after ?? null;
  let change: string | null = null;
  if (run.summary.route) change = explainChange(before?.summary.route ?? null, run.summary.route, run.response);
  else if (before) change = `Route no longer available. ${run.response.explanation}`.trim();
  return { before, after: run, change };
}

function RunView({ heading, run }: { heading: string; run: RouteRun | null }) {
  return (
    <div className="min-w-0 flex-1 space-y-1">
      <h5 className="text-sm font-semibold">{heading}</h5>
      {run ? (
        <>
          <p className="text-sm break-words">{run.summary.line}</p>
          {run.steps && (
            <ol className="list-decimal pl-5 text-sm">
              {run.steps.map((s, i) => (
                <li key={`${i}-${s}`}>{s}</li>
              ))}
            </ol>
          )}
        </>
      ) : (
        <p className="text-sm text-text-muted">No earlier result yet. Run again after the next change.</p>
      )}
    </div>
  );
}

/** Compact before/after comparison of two route calculations (SIMULATED). */
export function RouteComparison({ title, pair }: { title: string; pair: RouteRunPair }) {
  return (
    <div className="space-y-2 rounded border border-border-subtle p-2" data-testid="route-comparison">
      <h4 className="text-sm font-semibold">
        <StatusIcon kind="flask" /> {title} (SIMULATED)
      </h4>
      <div className="flex flex-col gap-3 sm:flex-row">
        <RunView heading="Before" run={pair.before} />
        <RunView heading="After" run={pair.after} />
      </div>
      {pair.change && <p className="text-sm" data-testid="route-change">{pair.change}</p>}
    </div>
  );
}
