export interface BottomControlsProps {
  onReportHazard: () => void;
  onRequestAssistance: () => void;
  onMarkSafe: () => void;
  onViewGuidance: () => void;
  /** Optional: the Map tab omits it (recalculate lives on the Route tab). */
  onRecalculate?: () => void;
  recalculating?: boolean;
}

const BTN =
  'min-h-[var(--target-min)] min-w-11 rounded-[var(--radius-card)] border border-border-subtle bg-surface px-3 py-2 text-center text-[length:var(--text-body)] font-semibold text-route shadow-[var(--shadow-card)] transition-colors duration-[var(--duration-fast)] active:bg-surface-raised focus:outline-2 focus:outline-route disabled:opacity-60';

/** Quick action row (Map tab). Every control is a labeled native button with a 44 px+ target. */
export function BottomControls({
  onReportHazard,
  onRequestAssistance,
  onMarkSafe,
  onViewGuidance,
  onRecalculate,
  recalculating = false,
}: BottomControlsProps) {
  return (
    <nav aria-label="Quick actions" data-testid="bottom-controls" className="grid grid-cols-2 gap-2 p-2">
      <button type="button" className={BTN} onClick={onReportHazard}>Report a hazard</button>
      <button type="button" className={BTN} onClick={onRequestAssistance}>Request assistance</button>
      <button type="button" className={BTN} onClick={onMarkSafe}>Mark safe</button>
      <button type="button" className={BTN} onClick={onViewGuidance}>View official guidance</button>
      {onRecalculate && (
        <button type="button" className={`${BTN} col-span-2`} onClick={onRecalculate} disabled={recalculating} aria-busy={recalculating}>
          {recalculating ? 'Recalculating…' : 'Recalculate route'}
        </button>
      )}
    </nav>
  );
}
