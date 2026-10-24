import { StatusIcon } from './StatusIcon';
export function SimulatedBadge() {
  return (
    <span
      data-testid="simulated-badge"
      className="inline-flex items-center gap-1 rounded border border-route-alt px-2 py-0.5 text-xs font-bold tracking-wide text-route-alt"
    >
      <StatusIcon kind="flask" />
      <span>SIMULATED</span>
    </span>
  );
}

