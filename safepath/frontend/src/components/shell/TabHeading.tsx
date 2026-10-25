import { TAB_TITLES, type TabId } from './navigation';

/**
 * Visually hidden heading at the top of each tab's content. A keyboard shortcut tab change moves
 * focus to the first heading of the new tab content (Requirement 16.10); this is that heading.
 * It is marked `data-focus-target` as a programmatic-only focus target (tabIndex -1).
 */
export function TabHeading({ tab }: { tab: TabId }) {
  return (
    <h2 data-focus-target="tab-heading" tabIndex={-1} className="sr-only">
      {TAB_TITLES[tab]}
    </h2>
  );
}
