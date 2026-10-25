import { OfficialLinks } from '../components/OfficialLinks';
import { Card } from '../components/ios/Card';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';
import { OfficialGuidance } from '../components/sidebar/OfficialGuidance';

/** Static official guidance with sources and retrieval dates. It is not a live order. */
export function OfficialGuidanceScreen({ hazard, onClose }: { hazard?: string | null; onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle('official-guidance')} onClose={onClose} testId="screen-official-guidance">
      <Card tone="warning" className="p-3 text-sm">
        <p>This is static preparedness guidance, not a live order. Follow instructions from official responders.</p>
      </Card>
      <OfficialGuidance hazard={hazard} />
      <OfficialLinks />
    </ScreenFrame>
  );
}
