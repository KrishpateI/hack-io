import type { MeInfo } from '../../../services/api';
import type { AppearanceSetting } from '../../../styles/appearance';
import { Card } from '../../ios/Card';
import { GroupedList } from '../../ios/GroupedList';
import { SegmentedControl, type SegmentedOption } from '../../ios/SegmentedControl';
import { ToggleSwitch } from '../../ios/ToggleSwitch';
import { OfficialLinks } from '../../OfficialLinks';
import { ProfileDetails } from '../ProfileMenu';
import { SCREEN_TITLES, screenHash } from '../screens';

export interface MoreTabProps {
  /** Shows the Demo console row only when true. */
  demoMode?: boolean;
  /** Account details; null renders no details list. */
  me: MeInfo | null;
  meLoading?: boolean;
  meError?: string | null;
  /** Appearance setting and its setter (from `useAppearance()`). */
  appearanceSetting: AppearanceSetting;
  onAppearanceChange: (setting: AppearanceSetting) => void;
  shortcutsEnabled: boolean;
  onShortcutsChange: (enabled: boolean) => void;
}

const APPEARANCE_OPTIONS: readonly SegmentedOption<AppearanceSetting>[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
];

/** More tab content: pure composition, no API calls of its own. */
export function MoreTab({
  demoMode = false,
  me,
  meLoading = false,
  meError = null,
  appearanceSetting,
  onAppearanceChange,
  shortcutsEnabled,
  onShortcutsChange,
}: MoreTabProps) {
  const rows = [
    { label: SCREEN_TITLES['incident-overview'], href: screenHash('incident-overview') },
    { label: SCREEN_TITLES['disaster-selection'], href: screenHash('disaster-selection') },
    { label: SCREEN_TITLES['data-sources'], href: screenHash('data-sources') },
    ...(demoMode ? [{ label: 'Demo console', href: '#/demo' }] : []),
  ];
  return (
    <div data-testid="more-tab" className="flex flex-col gap-4">
      <GroupedList aria-label="More" rows={rows} />

      <Card className="p-0">
        <OfficialLinks />
      </Card>

      <Card data-testid="more-account" className="text-sm">
        <h3 className="mb-2 text-sm font-semibold text-text-primary">Account</h3>
        {meLoading && <p role="status">Loading profile…</p>}
        {meError && (
          <p role="alert" className="text-closure">
            Could not load the profile: {meError}
          </p>
        )}
        {me && <ProfileDetails me={me} />}
        <p className="mt-2 text-xs text-text-muted">Other people see only your alias, never your account details.</p>
      </Card>

      <GroupedList header="Settings" aria-label="Settings">
        <div className="px-4 py-2.5">
          <div className="mb-1.5 text-[length:var(--text-body)] text-text-primary">Appearance</div>
          <SegmentedControl
            variant="radio"
            label="Appearance"
            idPrefix="appearance"
            options={APPEARANCE_OPTIONS}
            value={appearanceSetting}
            onChange={onAppearanceChange}
          />
        </div>
        <ToggleSwitch label="Keyboard shortcuts" checked={shortcutsEnabled} onChange={onShortcutsChange} />
      </GroupedList>

      <section aria-label="Keyboard shortcut list" data-testid="shortcut-list" className="px-4 text-sm text-text-muted">
        <p>
          Press <kbd>g</kbd> then <kbd>1</kbd> to <kbd>5</kbd> to switch tab: 1 Map, 2 Route, 3 Community, 4 Safety, 5
          More.
        </p>
      </section>
    </div>
  );
}
