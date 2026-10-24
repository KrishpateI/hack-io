import { OfficialLinks } from '../components/OfficialLinks';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';
import { OfficialGuidance } from '../components/sidebar/OfficialGuidance';

/** Static official guidance with sources and retrieval dates. It is not a live order. */
export function OfficialGuidanceScreen({ hazard, onClose }: { hazard?: string | null; onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle('official-guidance')} onClose={onClose} testId="screen-official-guidance">
      <p className="rounded border border-unverified p-2 text-sm">
        This is static preparedness guidance, not a live order. Follow instructions from official responders.
      </p>
      <OfficialGuidance hazard={hazard} />
      <OfficialLinks />
    </ScreenFrame>
  );
}
