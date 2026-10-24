import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';
import SourcesLimitations from './SourcesLimitations';

export function DataSourcesScreen({ onClose }: { onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle('data-sources')} onClose={onClose} testId="screen-data-sources">
      <SourcesLimitations showHeading={false} />
    </ScreenFrame>
  );
}
