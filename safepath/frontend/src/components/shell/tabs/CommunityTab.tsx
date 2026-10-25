import { GroupedList, GroupedRow } from '../../ios/GroupedList';
import { RightSidebarView, type RightSidebarViewProps } from '../RightSidebar';

export interface CommunityTabProps extends RightSidebarViewProps {
  /** Opens the pushed `community` screen (the "Community communications" row). */
  onOpenCommunications?: () => void;
}

const CommIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
  </svg>
);

/**
 * Community tab content: the Reports / Messages & notices / Check-ins & assistance
 * Segmented_Control (via `RightSidebarView`) plus the "Community communications" row.
 * Pure composition; calls no API.
 */
export function CommunityTab({ onOpenCommunications, ...sidebar }: CommunityTabProps) {
  return (
    <div data-testid="tab-community" className="space-y-3">
      <RightSidebarView {...sidebar} />
      {onOpenCommunications ? (
        <GroupedList aria-label="Community screens" className="px-3 pb-3">
          <GroupedRow label="Community communications" icon={<CommIcon />} onPress={onOpenCommunications} />
        </GroupedList>
      ) : null}
    </div>
  );
}
