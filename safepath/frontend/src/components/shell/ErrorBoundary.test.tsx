import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

vi.mock('../../AppShell', () => ({
  AppShell: () => {
    throw new Error('shell exploded');
  },
}));

import App from '../../App';

function Boom({ fail }: { fail: boolean }) {
  if (fail) throw new Error('boom');
  return <p>fine</p>;
}

describe('ErrorBoundary', () => {
  it('shows a safe fallback with 911 guidance and can retry', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    let fail = true;
    const { rerender } = render(
      <ErrorBoundary>
        <Boom fail={fail} />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('error-fallback')).toHaveTextContent('call 911');
    fail = false;
    rerender(
      <ErrorBoundary>
        <Boom fail={fail} />
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.getByText('fine')).toBeInTheDocument();
  });

  it('keeps the disclaimer and 911 guidance when the whole shell fails', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<App />);
    expect(screen.getByTestId('error-fallback')).toHaveTextContent('shell exploded');
    expect(screen.getByTestId('disclaimer-banner')).toBeInTheDocument();
    expect(screen.getByTestId('emergency-911')).toBeInTheDocument();
  });
});
