import type { TabIconName } from './icons';
import { CheckGlyph, TabIcon } from './icons';

export interface TabListItem<T extends string = string> {
  id: T;
  label: string;
  icon: TabIconName;
  /** Short text badge (shown with an icon, never color alone), e.g. "Updated". */
  badge?: string;
}

export interface TabListProps<T extends string = string> {
  tabs: readonly TabListItem<T>[];
  current: T;
  onSelect: (id: T) => void;
  /** Rail mode: labels are visually hidden but remain the accessible names. */
  rail?: boolean;
  className?: string;
}

/**
 * Vertical primary navigation for wide frames. The selected tab is a pill with a
 * filled icon, bold label, and a leading bar, so selection is never color-only.
 */
export function TabList<T extends string = string>({
  tabs,
  current,
  onSelect,
  rail = false,
  className = '',
}: TabListProps<T>) {
  return (
    <nav aria-label="Primary" className={className}>
      <ul className={`m-0 flex list-none flex-col gap-1 p-0 ${rail ? 'items-center' : ''}`}>
        {tabs.map((t) => {
          const selected = t.id === current;
          return (
            <li key={t.id} className={rail ? '' : 'w-full'}>
              <button
                type="button"
                onClick={() => onSelect(t.id)}
                aria-current={selected ? 'page' : undefined}
                data-selected={selected ? 'true' : 'false'}
                className={`relative flex min-h-[var(--target-min)] items-center gap-3 rounded-[var(--radius-control)] px-3 py-2 text-[length:var(--text-body)] transition-colors duration-[var(--duration-fast)] active:opacity-70 ${
                  rail ? 'justify-center' : 'w-full text-left'
                } ${
                  selected
                    ? 'bg-route/15 font-bold text-text-primary'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {selected ? (
                  <span
                    aria-hidden="true"
                    data-testid="tab-selected-bar"
                    className="absolute bottom-2 left-0 top-2 w-1 rounded-full bg-route"
                  />
                ) : null}
                <span className={selected ? 'text-route' : ''}>
                  <TabIcon
                    name={t.icon}
                    fill={selected ? 'currentColor' : 'none'}
                    fillOpacity={selected ? 0.3 : undefined}
                    strokeWidth={selected ? 2.2 : 1.8}
                  />
                </span>
                <span className={rail ? 'sr-only' : ''}>{t.label}</span>
                {t.badge ? (
                  <span
                    data-testid={`tab-badge-${t.id}`}
                    className={`inline-flex items-center gap-1 rounded-full border border-route px-1.5 text-[length:var(--text-caption)] font-semibold text-route ${
                      rail ? 'absolute right-0 top-0 bg-surface' : 'ml-auto'
                    }`}
                  >
                    <CheckGlyph size={10} />
                    {rail ? <span className="sr-only">{t.badge}</span> : t.badge}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
