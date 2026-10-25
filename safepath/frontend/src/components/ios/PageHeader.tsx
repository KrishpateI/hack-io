import { useEffect, useRef, useState, type ReactNode, type Ref, type RefObject } from 'react';
import { Chevron } from './icons';

export interface PageHeaderProps {
  title: string;
  /** Show the Large_Title (>= 28 px). When false only the compact title is shown. */
  large?: boolean;
  backLabel?: string;
  onBack?: () => void;
  /** The scrolling ancestor. Omit to observe against the viewport. */
  scrollRoot?: RefObject<HTMLElement | null>;
  /** Accessible name for the back control when it differs from the visible label. */
  backAriaLabel?: string;
  /** Control shown at the trailing edge of the compact bar (for example, Close). */
  trailing?: ReactNode;
  /** Id, ref, and level for the Large_Title heading. A ref makes the heading programmatically focusable. */
  headingId?: string;
  headingRef?: Ref<HTMLHeadingElement>;
  headingLevel?: 1 | 2;
  /** Classes for the wrapper around the Large_Title (for example, a max width). */
  titleWrapClassName?: string;
  className?: string;
}

/** Large title that collapses into a compact inline title once its sentinel scrolls away. */
export function PageHeader({
  title,
  large = true,
  backLabel,
  onBack,
  scrollRoot,
  backAriaLabel,
  trailing,
  headingId,
  headingRef,
  headingLevel = 1,
  titleWrapClassName = '',
  className = '',
}: PageHeaderProps) {
  const sentinel = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const el = sentinel.current;
    if (!large || !el) return;
    const root = scrollRoot?.current ?? null;

    if (typeof IntersectionObserver !== 'undefined') {
      const io = new IntersectionObserver(
        ([entry]) => setCollapsed(!entry.isIntersecting),
        { root, threshold: 0 },
      );
      io.observe(el);
      return () => io.disconnect();
    }

    // Fallback (e.g. jsdom): compare the sentinel against the root's top edge.
    const target: HTMLElement | Window = root ?? window;
    const check = () => {
      const top = root ? root.getBoundingClientRect().top : 0;
      setCollapsed(el.getBoundingClientRect().bottom <= top);
    };
    check();
    target.addEventListener('scroll', check, { passive: true });
    return () => target.removeEventListener('scroll', check);
  }, [large, scrollRoot]);

  const showCompact = !large || collapsed;
  const Heading = headingLevel === 2 ? 'h2' : 'h1';

  return (
    <div className={className}>
      <div
        className="glass sticky top-0 z-10 flex min-h-[var(--target-min)] items-center border-b border-border-subtle px-2"
        data-testid="compact-header"
      >
        <div className="w-24 shrink-0">
          {onBack ? (
            <button
              type="button"
              onClick={onBack}
              aria-label={backAriaLabel}
              className="inline-flex min-h-[var(--target-min)] items-center gap-0.5 rounded-[var(--radius-control)] px-2 text-[length:var(--text-body)] text-route transition-opacity duration-[var(--duration-fast)] active:opacity-50"
            >
              <Chevron direction="left" size={16} />
              <span>{backLabel ?? 'Back'}</span>
            </button>
          ) : null}
        </div>
        {/* With a Large_Title the compact copy is decorative; the h1 below is the heading. */}
        <div
          aria-hidden={large ? 'true' : undefined}
          role={large ? undefined : 'heading'}
          aria-level={large ? undefined : 1}
          className={`min-w-0 flex-1 truncate text-center text-[length:var(--text-headline)] font-semibold text-text-primary transition-opacity duration-[var(--duration-base)] ${
            showCompact ? 'opacity-100' : 'opacity-0'
          }`}
        >
          {title}
        </div>
        <div className="flex w-24 shrink-0 justify-end" aria-hidden={trailing ? undefined : 'true'}>
          {trailing}
        </div>
      </div>
      {large ? (
        <div className={titleWrapClassName}>
          <Heading
            id={headingId}
            ref={headingRef}
            tabIndex={headingRef ? -1 : undefined}
            className="m-0 px-4 pb-2 pt-3 text-[length:var(--text-large-title)] font-bold leading-tight tracking-tight text-text-primary focus:outline-2 focus:outline-route"
          >
            {title}
          </Heading>
          <div ref={sentinel} aria-hidden="true" data-testid="title-sentinel" className="h-px" />
        </div>
      ) : null}
    </div>
  );
}
