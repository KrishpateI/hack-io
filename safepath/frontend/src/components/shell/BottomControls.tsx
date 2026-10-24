export interface BottomControlsProps {
  onReportHazard: () => void;
  onRequestAssistance: () => void;
  onMarkSafe: () => void;
  onViewGuidance: () => void;
  onRecalculate: () => void;
  recalculating?: boolean;
}

const BTN =
  'min-h-12 min-w-11 flex-1 basis-36 rounded border border-route bg-surface-raised px-3 py-2 text-sm font-semibold text-text-primary focus:outline-2 focus:outline-route disabled:opacity-60';

/** Bottom action bar (Req 34.5). Every control is a labeled native button with a 44 px+ target. */
export function BottomControls({
  onReportHazard,
  onRequestAssistance,
  onMarkSafe,
  onViewGuidance,
  onRecalculate,
  recalculating = false,
}: BottomControlsProps) {
  return (
    <nav aria-label="Quick actions" data-testid="bottom-controls" className="flex flex-wrap gap-2 border-t border-border-subtle bg-surface p-2">
      <button type="button" className={BTN} onClick={onReportHazard}>Report a hazard</button>
      <button type="button" className={BTN} onClick={onRequestAssistance}>Request assistance</button>
      <button type="button" className={BTN} onClick={onMarkSafe}>Mark safe</button>
      <button type="button" className={BTN} onClick={onViewGuidance}>View official guidance</button>
      <button type="button" className={BTN} onClick={onRecalculate} disabled={recalculating} aria-busy={recalculating}>
        {recalculating ? 'Recalculating…' : 'Recalculate route'}
      </button>
    </nav>
  );
}
