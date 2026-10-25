import type { ComponentProps, ReactNode } from 'react';
import { FeatureInspector } from '../../inspect/FeatureInspector';
import { IndoorBuildingView } from '../../indoor/IndoorBuildingView';
import { Card } from '../../ios/Card';
import { GroupedList } from '../../ios/GroupedList';
import type { ViewportTier } from '../../ios/layout';
import { ActiveIncident } from '../../sidebar/ActiveIncident';
import { DisasterSelector } from '../../sidebar/DisasterSelector';
import { BottomControls } from '../BottomControls';
import { SCREEN_TITLES, type ScreenId } from '../screens';

export interface MapTabProps {
  tier: ViewportTier;
  /** Props for the ActiveIncident Card (`incident` may be null). */
  incident: ComponentProps<typeof ActiveIncident>['incident'];
  /** Quick action handlers (four actions; recalculate lives on the Route tab). */
  onReportHazard: () => void;
  onRequestAssistance: () => void;
  onMarkSafe: () => void;
  onViewGuidance: () => void;
  /** Inline disaster selector props, passed straight through (keeps `disaster-selector`). */
  disasterSelector: ComponentProps<typeof DisasterSelector>;
  /** Opens a pushed screen (used by the "Select a disaster" row). */
  onOpenScreen: (id: ScreenId) => void;
  /** `MapLayerControls` (layer toggles and legend), rendered as given. */
  layerControls?: ReactNode;
  /** Feature inspector props; rendered in the panel only when `tier` is below desktop. */
  inspector?: ComponentProps<typeof FeatureInspector>;
  /** True when the incident is a building; renders the indoor building view. */
  indoor?: boolean;
  /** Props for IndoorBuildingView (loader, reports, route, handlers). */
  indoorProps?: ComponentProps<typeof IndoorBuildingView>;
}

/** Map tab content: pure composition, no API calls of its own. */
export function MapTab({
  tier,
  incident,
  onReportHazard,
  onRequestAssistance,
  onMarkSafe,
  onViewGuidance,
  disasterSelector,
  onOpenScreen,
  layerControls,
  inspector,
  indoor = false,
  indoorProps,
}: MapTabProps) {
  return (
    <div data-testid="map-tab" className="flex flex-col gap-4">
      <Card className="p-0">
        <ActiveIncident incident={incident} />
      </Card>

      <BottomControls
        onReportHazard={onReportHazard}
        onRequestAssistance={onRequestAssistance}
        onMarkSafe={onMarkSafe}
        onViewGuidance={onViewGuidance}
      />

      <Card>
        <DisasterSelector {...disasterSelector} />
      </Card>

      <GroupedList
        aria-label="Disaster"
        rows={[{ label: SCREEN_TITLES['disaster-selection'], onPress: () => onOpenScreen('disaster-selection') }]}
      />

      {layerControls}

      {indoor && (
        <div data-testid="indoor-area">
          <IndoorBuildingView {...indoorProps} />
        </div>
      )}

      {tier !== 'desktop' && inspector && <FeatureInspector {...inspector} />}
    </div>
  );
}
