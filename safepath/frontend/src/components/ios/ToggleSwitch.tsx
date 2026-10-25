import { useId, type ReactNode } from 'react';

export interface ToggleSwitchProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: ReactNode;
  disabled?: boolean;
  id?: string;
  className?: string;
}

/** iOS switch: native checkbox with role="switch", sliding knob, visible label and On/Off text. */
export function ToggleSwitch({
  label,
  checked,
  onChange,
  description,
  disabled = false,
  id,
  className = '',
}: ToggleSwitchProps) {
  const auto = useId();
  const inputId = id ?? `switch-${auto}`;
  const descId = description ? `${inputId}-desc` : undefined;
  return (
    <div className={`flex min-h-[var(--target-min)] items-center gap-3 px-4 py-2.5 ${className}`}>
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="block text-[length:var(--text-body)] text-text-primary">
          {label}
        </label>
        {description ? (
          <div id={descId} className="text-[length:var(--text-caption)] text-text-muted">
            {description}
          </div>
        ) : null}
      </div>
      <span className="text-[length:var(--text-caption)] text-text-muted" data-testid="switch-state">
        {checked ? 'On' : 'Off'}
      </span>
      {/* The wrapper (the input's hit area) is at least --target-min tall; the 31 px track is centered in it. */}
      <span className="relative inline-flex h-[max(31px,var(--target-min))] w-[51px] shrink-0 items-center">
        <input
          id={inputId}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          aria-describedby={descId}
          onChange={(e) => onChange(e.target.checked)}
          className="peer absolute inset-0 z-10 m-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
        />
        <span
          aria-hidden="true"
          className={`absolute inset-x-0 top-1/2 h-[31px] -translate-y-1/2 rounded-full border transition-colors duration-[var(--duration-base)] peer-focus-visible:outline peer-focus-visible:outline-[3px] peer-focus-visible:outline-offset-2 peer-focus-visible:outline-route peer-disabled:opacity-50 ${
            checked ? 'border-verified bg-verified' : 'border-control-border bg-surface-raised'
          }`}
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-0.5 top-1/2 -mt-[12.5px] h-[25px] w-[25px] rounded-full border border-border-subtle bg-text-primary shadow-[var(--shadow-card)] transition-transform duration-[var(--duration-base)] ease-[var(--ease-standard)] peer-disabled:opacity-50"
          style={{ transform: `translateX(${checked ? 20 : 0}px)` }}
        />
      </span>
    </div>
  );
}
