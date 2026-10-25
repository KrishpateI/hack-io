import { CheckGlyph, TabIcon, type TabIconName } from './icons';

export interface NavTab<T extends string = string> {
  id: T;
  label: string;
  icon: TabIconName;
  /** Short text badge (shown with an icon, never color alone), e.g. "Updated". */
  badge?: string;
}

export interface TabBarProps<T extends string = string> {
  tabs: readonly NavTab<T>[];
  current: T;
  onSelect: (id: T) => void;
  className?: string;
}

/** Compact bottom tab bar: icon over label, glass background, safe-area padding. */
export function TabBar<T extends string = string>({
  tabs,
  current,
  onSelect,
  className = '',
}: TabBarProps<T>) {
  return (
    <nav
      aria-label="Primary"
      className={`glass border-t border-border-subtle pb-[env(safe-area-inset-bottom)] ${className}`}
    >
      <ul className="m-0 flex list-none p-0">
        {tabs.map((tab) => {
          const selected = tab.id === current;
          return (
            <li key={tab.id} className="min-w-0 flex-1">
              <button
                type="button"
                aria-current={selected ? 'page' : undefined}
                onClick={() => onSelect(tab.id)}
                className={`relative flex min-h-[var(--target-min)] w-full flex-col items-center justify-center gap-0.5 px-1 py-1.5 transition-opacity duration-[var(--duration-fast)] active:opacity-70 ${
                  selected ? 'font-semibold text-route' : 'text-text-muted'
                }`}
              >
                <TabIcon name={tab.icon} />
                <span className="max-w-full truncate text-[length:var(--text-caption)]">
                  {tab.label}
                </span>
                {tab.badge ? (
                  <span
                    data-testid={`tab-badge-${tab.id}`}
                    className="absolute right-0.5 top-0.5 inline-flex items-center gap-0.5 rounded-full border border-route bg-surface px-1 text-[10px] font-semibold leading-4 text-route"
                  >
                    <CheckGlyph size={8} />
                    {tab.badge}
                  </span>
                ) : null}
                {selected ? (
                  <span
                    aria-hidden="true"
                    data-testid="tab-selected-indicator"
                    className="h-0.5 w-4 rounded-full bg-route"
                  />
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
