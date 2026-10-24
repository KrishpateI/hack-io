import type { ReactNode } from 'react';
import { DisclaimerBanner } from './DisclaimerBanner';
import { Emergency911Panel } from './Emergency911Panel';
import { Header, type HeaderProps } from './Header';
import type { ActiveIncidentLike } from '../services/labels';

export interface RootLayoutProps {
  children?: ReactNode;
  incidents?: readonly ActiveIncidentLike[];
  headerProps?: Omit<HeaderProps, 'incidents'>;
  /** Persistent status area under the disclaimer (offline banner, queue). */
  banner?: ReactNode;
}

/**
 * Persistent chrome: skip link, disclaimer, header, one main landmark and the 911 panel.
 * Nothing here can be dismissed or hidden by screens (Req 1.1, 1.3).
 */
export function RootLayout({ children, incidents, headerProps, banner }: RootLayoutProps) {
  return (
    <div className="flex min-h-screen min-w-[320px] max-w-full flex-col overflow-x-clip bg-base text-text-primary">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <DisclaimerBanner />
      {banner}
      <Header incidents={incidents} {...headerProps} />
      <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 focus:outline-none">
        {children}
      </main>
      <Emergency911Panel />
    </div>
  );
}
