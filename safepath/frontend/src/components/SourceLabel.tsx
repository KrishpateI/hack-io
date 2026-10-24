import { StatusIcon, type IconKind } from './StatusIcon';
import { classify, type LabelableItem } from '../services/labels';

const STYLE: Record<ReturnType<typeof classify>, { icon: IconKind; cls: string }> = {
  Official: { icon: 'check', cls: 'border-verified text-verified' },
  Simulated: { icon: 'flask', cls: 'border-route-alt text-route-alt' },
  Community: { icon: 'people', cls: 'border-unverified text-unverified' },
  'Unknown source': { icon: 'question', cls: 'border-border-subtle text-text-muted' },
};

/** Shows exactly one source label as text (Req 10.4, 10.5, 36.5). */
export function SourceLabel({ item }: { item: LabelableItem | null | undefined }) {
  const label = classify(item);
  const s = STYLE[label];
  return (
    <span data-testid="source-label" className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${s.cls}`}>
      <StatusIcon kind={s.icon} />
      <span>{label}</span>
    </span>
  );
}

