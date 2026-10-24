import type { RouteRecalcSnapshot } from '../../services/routeRecalc';
import { StatusIcon } from '../StatusIcon';

export const STALE_MESSAGE = 'Road conditions may be out of date';

function fmtDistance(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}
function fmtMinutes(s: number): string {
  return `${Math.max(1, Math.round(s / 60))} min`;
}

/**
 * Recalculation feedback (Req 16, 34): updating state with the old route flagged as possibly outdated,
 * change explanation with previous vs new distance/time, failure message, and the stale-data warning.
 */
export function RouteChangeBanner({ recalc }: { recalc: RouteRecalcSnapshot | null | undefined }) {
  if (!recalc) return null;
  const { route, previousRoute, change, status, updating, message, stale } = recalc;
  const showCompare = !!(route && previousRoute && !updating);
  if (!stale && !updating && !change && !message && status === 'idle') return null;
  return (
    <div data-testid="route-change-banner" className="space-y-2" aria-live="polite">
      {stale && (
        <p
          data-testid="stale-warning"
          role="alert"
          className="flex items-start gap-2 rounded border border-unverified p-2 text-sm text-unverified"
        >
          <StatusIcon kind="warning" />
          <span>{STALE_MESSAGE}</span>
        </p>
      )}
      {updating && (
        <p data-testid="route-updating" role="status" className="flex items-start gap-2 rounded border border-route p-2 text-sm text-route">
          <StatusIcon kind="question" />
          <span>Updating… The route shown below may be outdated until the new result arrives.</span>
        </p>
      )}
      {status === 'failed' && message && (
        <p data-testid="route-recalc-failed" role="alert" className="flex items-start gap-2 rounded border border-closure p-2 text-sm text-closure">
          <StatusIcon kind="cross" />
          <span>{message}</span>
        </p>
      )}
      {status === 'no_route' && message && (
        <p data-testid="route-invalid" role="alert" className="flex items-start gap-2 rounded border border-closure p-2 text-sm text-closure">
          <StatusIcon kind="cross" />
          <span>{message}</span>
        </p>
      )}
      {!updating && change && (
        <section data-testid="route-change" aria-label="Route change" className="rounded border border-border-subtle bg-surface-raised p-2 text-sm">
          <h3 className="font-semibold">Route updated</h3>
          <p>{change}</p>
          {showCompare && route && previousRoute && (
            <dl className="mt-1 grid grid-cols-[max-content_1fr_1fr] gap-x-3 text-xs">
              <dt />
              <dd className="font-semibold">Previous</dd>
              <dd className="font-semibold">New</dd>
              <dt className="text-text-muted">Distance</dt>
              <dd>{fmtDistance(previousRoute.distance_meters)}</dd>
              <dd>{fmtDistance(route.distance_meters)}</dd>
              <dt className="text-text-muted">Time</dt>
              <dd>{fmtMinutes(previousRoute.estimated_duration_seconds)}</dd>
              <dd>{fmtMinutes(route.estimated_duration_seconds)}</dd>
            </dl>
          )}
        </section>
      )}
    </div>
  );
}
