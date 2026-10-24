import { CheckInPanel, type CheckInPanelProps } from '../components/community/CheckInPanel';
import { CALL_911_TEXT, NO_DISPATCH_TEXT } from '../components/community/NoDispatchNotice';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';

/** Full-screen safety check-in (Req 27, 1.3, 1.4). */
export function SafetyCheckInScreen({ onClose, ...panel }: CheckInPanelProps & { onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle('safety-check-in')} onClose={onClose} testId="screen-safety-check-in">
      <section aria-labelledby="checkin-privacy-h" className="rounded border border-border-subtle p-3 text-sm">
        <h2 id="checkin-privacy-h" className="mb-1 text-base font-semibold">Privacy</h2>
        <p>
          A check-in shares your status with the incident. Your location is only included if you choose to share it, and it is
          removed after 72 hours. Other people see you only by an alias.
        </p>
      </section>
      <CheckInPanel {...panel} />
      <aside
        aria-labelledby="checkin-911-h"
        data-testid="emergency-contact-guidance"
        className="rounded-lg border-2 border-closure bg-surface-raised p-3 text-sm"
      >
        <h2 id="checkin-911-h" className="mb-1 text-base font-bold text-closure">In an emergency</h2>
        <p className="font-semibold">{CALL_911_TEXT}</p>
        <p className="mt-1">{NO_DISPATCH_TEXT}</p>
        <p className="mt-1 text-text-muted">
          A check-in is not a call for help. To reach family or friends, contact them directly by phone or message.
        </p>
      </aside>
    </ScreenFrame>
  );
}
