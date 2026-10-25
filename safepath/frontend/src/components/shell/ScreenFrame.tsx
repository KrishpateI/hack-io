import { useContext, useEffect, useId, useRef, type ReactNode } from 'react';
import { useDismissibleLayer } from '../ios/hooks/useDismissibleLayer';
import { PageHeader } from '../ios/PageHeader';
import { ScreenBackContext } from './screenBack';

export interface ScreenFrameProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
  /** Title of the previous screen. With `onBack`, shows a back control named `Back to <title>`. */
  backTitle?: string;
  onBack?: () => void;
}

/**
 * Screen shown in the main content area. It is rendered in normal flow below the persistent
 * disclaimer banner and header, so the disclaimer, SIMULATED indicator and 911 guidance stay
 * visible on every screen (Req 1.1, 1.2). It is a non-modal dialog: on open focus moves to the
 * heading, Escape closes it through the shared layer stack, Tab is not trapped, and focus
 * returns to the opener on close (Req 6.6, 6.7, 16.7).
 *
 * The heading is a Large_Title that collapses into a compact inline title in the sticky bar once
 * it scrolls away. Text width is capped at 720 px above 1600 px (Req 9.6).
 */
export function ScreenFrame({ title, onClose, children, testId, backTitle: backTitleProp, onBack: onBackProp }: ScreenFrameProps) {
  const inherited = useContext(ScreenBackContext);
  const backTitle = backTitleProp ?? inherited.backTitle;
  const onBack = onBackProp ?? inherited.onBack;
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Registered before the focus effect so the layer records the opener, not the heading.
  useDismissibleLayer(true, onClose);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    headingRef.current?.focus();
    return () => {
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
    };
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby={headingId}
      data-testid={testId ?? 'screen-frame'}
      className="relative flex min-h-[60vh] min-w-0 flex-col bg-base pb-24 text-text-primary"
    >
      <PageHeader
        title={title}
        headingId={headingId}
        headingRef={headingRef}
        headingLevel={2}
        backLabel={backTitle && onBack ? backTitle : undefined}
        backAriaLabel={backTitle && onBack ? `Back to ${backTitle}` : undefined}
        onBack={backTitle && onBack ? onBack : undefined}
        trailing={
          <button
            type="button"
            onClick={onClose}
            className="min-h-[var(--target-min)] min-w-[var(--target-min)] rounded-[var(--radius-control)] px-3 text-[length:var(--text-body)] font-semibold text-route focus:outline-2 focus:outline-route active:opacity-50"
          >
            Close<span className="sr-only"> {title}</span>
          </button>
        }
        titleWrapClassName="mx-auto w-full max-w-3xl min-[1601px]:max-w-[720px]"
        className="contents"
      />
      <div className="min-w-0 flex-1 p-3">
        <div className="mx-auto w-full max-w-3xl space-y-4 min-[1601px]:max-w-[720px]">{children}</div>
      </div>
    </div>
  );
}