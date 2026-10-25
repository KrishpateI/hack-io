import { Card } from '../../ios/Card';
import { GroupedRow } from '../../ios/GroupedList';
import { SegmentedControl } from '../../ios/SegmentedControl';
import { ToggleSwitch } from '../../ios/ToggleSwitch';
import { RoutePanel, type RoutePanelProps } from '../../route/RoutePanel';
import { RouteUncertaintySummary } from '../../sidebar/RouteUncertaintySummary';
import { StatusIcon } from '../../StatusIcon';
import { StartDestinationPicker, type Destination, type LatLng } from '../../sidebar/StartDestinationPicker';

export type TravelMode = 'walking' | 'wheelchair' | 'driving';

export const TRAVEL_MODES: readonly { value: TravelMode; label: string }[] = [
  { value: 'walking', label: 'Walking' },
  { value: 'wheelchair', label: 'Wheelchair' },
  { value: 'driving', label: 'Driving' },
];

export interface RouteTabProps {
  start: LatLng | null;
  destination: Destination | null;
  pickMode: 'start' | 'destination' | null;
  onPickModeChange: (m: 'start' | 'destination' | null) => void;
  onStartChange: (p: LatLng) => void;
  onDestinationChange: (p: Destination) => void;
  /** Offer the flood-scenario sample points (true when the selected type is flood). */
  showSamplePoints?: boolean;
  travelMode: TravelMode;
  onTravelModeChange: (m: TravelMode) => void;
  stepFree: boolean;
  onStepFreeChange: (v: boolean) => void;
  /** Indoor incident: travel mode and step-free controls are hidden, as in the old layout. */
  indoor?: boolean;
  /** Policy outcome is SHELTER_IN_PLACE: shows `shelter-note` at the top. */
  shelter?: boolean;
  /** Props for `RoutePanel` (response, loading, error, recalc, selection, onRecalculate, sourceRecords). */
  routePanel: RoutePanelProps;
  /** Opens the pushed Route details screen. */
  onOpenRouteDetails: () => void;
}

/** Route tab: setup (points, mode, step-free), uncertainty, results and the Route details row. Props only. */
export function RouteTab(p: RouteTabProps) {
  return (
    <div data-testid="route-tab" className="space-y-3">
      {p.shelter && (
        <div className="px-3">
          <Card
            tone="danger"
            role="status"
            data-testid="shelter-note"
            className="flex items-start gap-2 p-3 text-[length:var(--text-body)] font-semibold"
          >
            <StatusIcon kind="warning" />
            <span>Shelter in place: no route is shown. Follow the guidance below.</span>
          </Card>
        </div>
      )}

      <StartDestinationPicker
        start={p.start}
        destination={p.destination}
        pickMode={p.pickMode}
        onPickModeChange={p.onPickModeChange}
        onStartChange={p.onStartChange}
        onDestinationChange={p.onDestinationChange}
        showSamplePoints={p.showSamplePoints}
      />

      {!p.indoor && (
        <div className="space-y-2 px-3">
          <SegmentedControl<TravelMode>
            variant="radio"
            label="Travel mode"
            idPrefix="travel-mode"
            options={TRAVEL_MODES}
            value={p.travelMode}
            onChange={p.onTravelModeChange}
          />
          <div className="overflow-hidden rounded-[var(--radius-card)] border border-border-subtle bg-surface">
            <ToggleSwitch id="step-free" label="Step-free route" checked={p.stepFree} onChange={p.onStepFreeChange} />
          </div>
        </div>
      )}

      <RouteUncertaintySummary response={p.routePanel.response} />

      <RoutePanel {...p.routePanel} />

      <div className="px-3">
        <div className="overflow-hidden rounded-[var(--radius-card)] border border-border-subtle bg-surface">
          <GroupedRow label="Route details" onPress={p.onOpenRouteDetails} />
        </div>
      </div>
    </div>
  );
}
