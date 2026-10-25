import type { HTMLAttributes, ReactNode } from 'react';

export type CardTone = 'default' | 'info' | 'warning' | 'danger' | 'success';

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  tone?: CardTone;
  children: ReactNode;
}

// A tone adds a leading accent bar plus a tinted border; meaning is never color-only
// because callers include text. Radius comes from --radius-card (14 px).
const TONE: Record<CardTone, string> = {
  default: 'border-border-subtle',
  info: 'border-route border-l-4',
  warning: 'border-unverified border-l-4',
  danger: 'border-closure border-l-4',
  success: 'border-verified border-l-4',
};

export function Card({ tone = 'default', className = '', children, ...rest }: CardProps) {
  return (
    <div
      {...rest}
      data-tone={tone}
      className={`rounded-[var(--radius-card)] border bg-surface p-4 text-text-primary shadow-[var(--shadow-card)] ${TONE[tone]} ${className}`}
    >
      {children}
    </div>
  );
}
