import { useId, type ReactNode } from 'react';
import { Chevron } from './icons';

export interface GroupedRowProps {
  label: string;
  value?: ReactNode;
  icon?: ReactNode;
  /** When set, the row navigates and renders an `a`. */
  href?: string;
  /** When set (and no href), the row renders a `button`. */
  onPress?: () => void;
  /** Replaces the default chevron (shown only for interactive rows). */
  trailing?: ReactNode;
  /** Non-interactive row (no href, no onPress). */
  className?: string;
}

const ROW_BASE =
  'flex w-full min-h-[var(--target-min)] items-center gap-3 px-4 py-2.5 text-left text-[length:var(--text-body)] text-text-primary';
const PRESSABLE =
  'transition-colors duration-[var(--duration-fast)] active:bg-surface-raised';

export function GroupedRow({ label, value, icon, href, onPress, trailing, className = '' }: GroupedRowProps) {
  const interactive = Boolean(href || onPress);
  const valueId = useId();
  const hasValue = value !== undefined && value !== null;
  const describedBy = hasValue ? valueId : undefined;
  const content = (
    <>
      {icon ? (
        <span aria-hidden="true" className="flex shrink-0 items-center text-route">
          {icon}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">{label}</span>
      {hasValue ? (
        <span id={valueId} className="shrink-0 text-text-muted">
          {value}
        </span>
      ) : null}
      {trailing ??
        (interactive ? (
          <span aria-hidden="true" className="flex shrink-0 items-center text-text-muted">
            <Chevron />
          </span>
        ) : null)}
    </>
  );
  const cls = `${ROW_BASE} ${interactive ? PRESSABLE : ''} ${className}`;

  if (href) {
    return (
      <a href={href} onClick={onPress} aria-label={label} aria-describedby={describedBy} className={cls}>
        {content}
      </a>
    );
  }
  if (onPress) {
    return (
      <button type="button" onClick={onPress} aria-label={label} aria-describedby={describedBy} className={cls}>
        {content}
      </button>
    );
  }
  return <div className={cls}>{content}</div>;
}

export interface GroupedListProps {
  /** Rows as data; alternatively pass `children` (GroupedRow or any element). */
  rows?: GroupedRowProps[];
  children?: ReactNode;
  /** Small uppercase caption above the group. */
  header?: string;
  /** Explanatory caption below the group. */
  footer?: ReactNode;
  className?: string;
  'aria-label'?: string;
}

/** Inset grouped list: rounded surface with hairline separators between rows. */
export function GroupedList({ rows, children, header, footer, className = '', ...rest }: GroupedListProps) {
  const items: ReactNode[] = [
    ...(rows ?? []).map((r) => <GroupedRow key={`${r.label}-${r.href ?? ''}`} {...r} />),
  ];
  const extra = Array.isArray(children) ? children : children ? [children] : [];
  const all = [...items, ...extra];
  return (
    <section className={className} aria-label={rest['aria-label']}>
      {header ? (
        <h3 className="mb-1.5 px-4 text-[length:var(--text-caption)] font-medium uppercase tracking-wide text-text-muted">
          {header}
        </h3>
      ) : null}
      <ul className="m-0 list-none overflow-hidden rounded-[var(--radius-card)] border border-border-subtle bg-surface p-0 divide-y divide-border-subtle">
        {all.map((child, i) => (
          <li key={i} className="m-0 p-0">
            {child}
          </li>
        ))}
      </ul>
      {footer ? (
        <p className="mt-1.5 px-4 text-[length:var(--text-caption)] text-text-muted">{footer}</p>
      ) : null}
    </section>
  );
}
