export const HISTORICAL_TEXT = 'Historical reference (2007), not a current evacuation order';

/** Shown next to every CBD-derived item (Req 8.10). */
export function HistoricalLabel() {
  return (
    <span
      data-testid="historical-label"
      className="inline-flex items-center rounded border border-unverified px-1.5 py-0.5 text-xs text-unverified"
    >
      {HISTORICAL_TEXT}
    </span>
  );
}
