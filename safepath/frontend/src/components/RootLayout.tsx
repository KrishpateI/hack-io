import type { ReactNode } from 'react';
import { DisclaimerBanner } from './DisclaimerBanner';
import { Emergency911Panel } from './Emergency911Panel';
import { Header } from './Header';
import type { ActiveIncidentLike } from '../services/labels';

export function RootLayout({ children, incidents }: { children?: ReactNode; incidents?: readonly ActiveIncidentLike[] }) {
  return (
    <div className="flex min-h-screen min-w-[320px] flex-col bg-base text-text-primary">
      <DisclaimerBanner />
      <Header incidents={incidents} />
      <main className="flex-1">{children}</main>
      <Emergency911Panel />
    </div>
  );
}
