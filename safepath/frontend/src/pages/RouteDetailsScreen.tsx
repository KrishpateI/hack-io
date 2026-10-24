import { RoutePanel, type RoutePanelProps } from '../components/route/RoutePanel';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';

/** Full-screen route details: the same RoutePanel content (steps, explanation, uncertainty, sources). */
export function RouteDetailsScreen({ onClose, ...panel }: RoutePanelProps & { onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle('route-details')} onClose={onClose} testId="screen-route-details">
      <RoutePanel {...panel} />
    </ScreenFrame>
  );
}
