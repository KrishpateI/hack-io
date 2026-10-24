import { DISCLAIMER_TEXT } from '../styles/tokens';

/** Always visible. No dismiss control and no conditional rendering (Req 1.1). */
export function DisclaimerBanner() {
  return (
    <div
      role="note"
      aria-label="Disclaimer"
      data-testid="disclaimer-banner"
      className="w-full break-words border-b border-unverified bg-surface-raised px-3 py-2 text-xs text-text-primary sm:text-sm"
    >
      {DISCLAIMER_TEXT}
    </div>
  );
}
