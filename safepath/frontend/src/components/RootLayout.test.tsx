import { render, screen, within } from '@testing-library/react';
import fc from 'fast-check';
import { RootLayout } from './RootLayout';
import { DISCLAIMER_TEXT } from '../styles/tokens';

const EXACT =
  'SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies.';

describe('RootLayout', () => {
  it('renders the exact disclaimer with no dismiss control', () => {
    render(<RootLayout />);
    expect(DISCLAIMER_TEXT).toBe(EXACT);
    expect(screen.getByTestId('disclaimer-banner')).toHaveTextContent(EXACT);
    expect(within(screen.getByTestId('disclaimer-banner')).queryByRole('button')).toBeNull();
  });

  it('renders header with a simulated indicator slot', () => {
    render(<RootLayout />);
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByTestId('simulated-indicator-slot')).toBeInTheDocument();
  });

  it('keeps the disclaimer for any child content', () => {
    fc.assert(
      fc.property(fc.string(), (s) => {
        const { unmount } = render(<RootLayout>{s}</RootLayout>);
        const ok = screen.getAllByTestId('disclaimer-banner').length === 1;
        unmount();
        return ok;
      }),
      { numRuns: 25 },
    );
  });
});
