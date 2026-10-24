import type { RouteResponse } from '../../services/api';
import { HistoricalLabel } from '../HistoricalLabel';
import { StatusIcon } from '../StatusIcon';

/** Uncertainty level, confidence limitations and the algorithm-generated label for a route response (Req 1, 15). */
export function RouteUncertaintySummary({ response }: { response: RouteResponse | null }) {
  const route = response?.routes?.[0];
  return (
    <section aria-labelledby="uncertainty-h" data-testid="route-uncertainty" className="p-3">
      <h2 id="uncertainty-h" className="mb-2 text-sm font-semibold text-text-primary">Route uncertainty</h2>
      {!response || !route ? (
        <p className="text-sm text-text-muted">No route calculated yet.</p>
      ) : (
        <div className="text-sm">
          <p className="flex items-center gap-1">
            <StatusIcon kind={route.uncertainty_level === 'Low' ? 'check' : 'warning'} />
            <span>Uncertainty level: <strong>{route.uncertainty_level}</strong></span>
          </p>
          <p className="mt-1 text-text-muted">{route.label ?? response.label ?? 'algorithm-generated'}</p>
          {route.confidence_limitations.length > 0 ? (
            <ul className="mt-1 list-disc pl-5">{route.confidence_limitations.map((c) => <li key={c}>{c}</li>)}</ul>
          ) : (
            <p className="mt-1 text-text-muted">No confidence limitations were reported. Conditions may still differ from this estimate.</p>
          )}
          {response.notices.some((n) => n.cbd_derived) && (
            <p className="mt-2 flex flex-wrap items-center gap-2"><HistoricalLabel /><span className="text-xs text-text-muted">Includes content derived from a historical document.</span></p>
          )}
        </div>
      )}
    </section>
  );
}
