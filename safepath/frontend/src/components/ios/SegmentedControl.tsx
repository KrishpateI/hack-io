import { useRef, type KeyboardEvent } from 'react';
import { CheckGlyph } from './icons';

export interface SegmentedOption<T extends string = string> {
  value: T;
  label: string;
}

export interface SegmentedControlProps<T extends string = string> {
  variant: 'tabs' | 'radio';
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Accessible name of the tablist / radiogroup. */
  label: string;
  /** Prefix for generated ids (tabs get `${idPrefix}-tab-${value}`). */
  idPrefix?: string;
  className?: string;
}

/** iOS segmented pill: a sliding selected thumb under equal-width segments. */
export function SegmentedControl<T extends string = string>({
  variant,
  options,
  value,
  onChange,
  label,
  idPrefix = 'seg',
  className = '',
}: SegmentedControlProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const n = options.length;
  const found = options.findIndex((o) => o.value === value);
  const selected = found < 0 ? 0 : found;
  const isTabs = variant === 'tabs';

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (index + 1) % n;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (index - 1 + n) % n;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = n - 1;
    else return;
    e.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role={isTabs ? 'tablist' : 'radiogroup'}
      aria-label={label}
      className={`relative grid rounded-[var(--radius-control)] border border-border-subtle bg-base p-0.5 ${className}`}
      style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}
    >
      {n > 0 ? (
        <span
          aria-hidden="true"
          data-testid="segment-thumb"
          className="pointer-events-none absolute bottom-0.5 left-0.5 top-0.5 rounded-[calc(var(--radius-control)-2px)] border border-control-border bg-surface-raised shadow-[var(--shadow-card)] transition-transform duration-[var(--duration-base)] ease-[var(--ease-standard)]"
          style={{
            width: `calc((100% - 4px) / ${n})`,
            transform: `translateX(${selected * 100}%)`,
          }}
        />
      ) : null}
      {options.map((o, i) => {
        const isSel = i === selected;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            id={isTabs ? `${idPrefix}-tab-${o.value}` : undefined}
            role={isTabs ? 'tab' : 'radio'}
            {...(isTabs ? { 'aria-selected': isSel } : { 'aria-checked': isSel })}
            tabIndex={isSel ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={`relative z-10 flex min-h-[var(--target-min)] items-center justify-center gap-1 rounded-[calc(var(--radius-control)-2px)] px-2 text-[length:var(--text-body)] transition-colors duration-[var(--duration-fast)] active:opacity-70 ${
              isSel ? 'font-semibold text-text-primary' : 'text-text-muted'
            }`}
          >
            {isSel ? <CheckGlyph size={12} /> : null}
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
