import { StatusIcon } from '../StatusIcon';

export const NO_DISPATCH_TEXT = 'SafePath does not dispatch emergency services and is not connected to 911.';
export const CALL_911_TEXT = 'If you are in immediate danger, call 911 from your phone now.';

/** No-dispatch statement plus separate 911 guidance (Req 26.5, 27.6). */
export function NoDispatchNotice() {
  return (
    <div data-testid="no-dispatch-notice" role="note" className="space-y-1 rounded border-2 border-closure bg-base p-2 text-sm">
      <p className="flex items-start gap-1.5 text-text-primary">
        <span className="mt-1 text-closure"><StatusIcon kind="warning" /></span>
        <span>{NO_DISPATCH_TEXT}</span>
      </p>
      <p data-testid="guidance-911" className="font-semibold text-text-primary">{CALL_911_TEXT}</p>
    </div>
  );
}
