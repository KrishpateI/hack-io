import { Card } from '../../ios/Card';
import { GroupedList } from '../../ios/GroupedList';
import { Emergency911Guidance } from '../../Emergency911Guidance';
import { OfficialGuidance } from '../../sidebar/OfficialGuidance';

export interface SafetyTabProps {
  /** Opens the `submit-report` pushed screen. */
  onReportHazard: () => void;
  /** Opens the `community` pushed screen (assistance request). */
  onRequestAssistance: () => void;
  /** Opens the `safety-check-in` pushed screen. */
  onMarkSafe: () => void;
  /** Opens the `official-guidance` pushed screen. */
  onViewGuidance: () => void;
  /** Hazard of the selected disaster; filters the guidance entries. */
  hazard?: string | null;
}

function RowIcon({ children }: { children: React.ReactNode }) {
  return (
    <svg
      width={22}
      height={22}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

const ICONS = {
  report: (
    <RowIcon>
      <path d="M12 3 2.5 20h19z" />
      <path d="M12 10v4.5M12 17.5v.01" />
    </RowIcon>
  ),
  assist: (
    <RowIcon>
      <path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" />
    </RowIcon>
  ),
  safe: (
    <RowIcon>
      <circle cx="12" cy="12" r="9" />
      <path d="m8 12.2 2.8 2.8L16 9.5" />
    </RowIcon>
  ),
  guidance: (
    <RowIcon>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M14 3v5h5M9 13h7M9 17h7" />
    </RowIcon>
  ),
};

/** Safety tab: action rows, the 911 Card, and static official guidance. Props only; no API calls. */
export function SafetyTab({ onReportHazard, onRequestAssistance, onMarkSafe, onViewGuidance, hazard }: SafetyTabProps) {
  return (
    <div className="space-y-4" data-testid="safety-tab">
      <GroupedList
        aria-label="Safety actions"
        rows={[
          { label: 'Report a hazard', icon: ICONS.report, onPress: onReportHazard },
          { label: 'Request assistance', icon: ICONS.assist, onPress: onRequestAssistance },
          { label: 'Mark safe', icon: ICONS.safe, onPress: onMarkSafe },
          { label: 'View official guidance', icon: ICONS.guidance, onPress: onViewGuidance },
        ]}
      />
      <Card tone="danger" data-testid="safety-911-card" role="region" aria-labelledby="safety-911-h">
        <h2 id="safety-911-h" className="mb-1 text-[length:var(--text-body)] font-semibold text-text-primary">
          Emergency: 911
        </h2>
        <Emergency911Guidance />
      </Card>
      <OfficialGuidance hazard={hazard} />
    </div>
  );
}
