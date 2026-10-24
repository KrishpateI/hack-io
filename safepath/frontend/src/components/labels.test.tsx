import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import { classify, labelAttribute } from '../services/labels';
import { RootLayout } from './RootLayout';
import { SourceLabel } from './SourceLabel';
import { StatusBadge } from './StatusBadge';

describe('labels', () => {
  it('classifies by precedence', () => {
    expect(classify({ source_record: { is_official: true }, simulation_flag: true })).toBe('Official');
    expect(classify({ simulation_flag: true, origin: 'report' })).toBe('Simulated');
    expect(classify({ origin: 'message' })).toBe('Community');
    expect(classify({})).toBe('Unknown source');
    expect(classify(null)).toBe('Unknown source');
  });
  it('labelAttribute renders Unknown for missing values', () => {
    expect(labelAttribute(undefined)).toBe('Unknown');
    expect(labelAttribute(null)).toBe('Unknown');
    expect(labelAttribute('  ')).toBe('Unknown');
    expect(labelAttribute(0)).toBe('0');
  });
  it('SourceLabel shows exactly one label for any item', () => {
    fc.assert(
      fc.property(
        fc.record({
          source_record: fc.option(fc.record({ is_official: fc.option(fc.boolean()) }), { nil: undefined }),
          simulation_flag: fc.option(fc.boolean(), { nil: undefined }),
          origin: fc.option(fc.constantFrom('report', 'message', 'x'), { nil: undefined }),
        }),
        (item) => {
          const { unmount, container } = render(<SourceLabel item={item} />);
          const ok = container.textContent!.includes(classify(item)) && screen.getAllByTestId('source-label').length === 1;
          unmount();
          return ok;
        },
      ),
      { numRuns: 25 },
    );
  });
});

describe('StatusBadge', () => {
  it('has icon (svg) and text, never color alone', () => {
    render(<StatusBadge status="closed" />);
    expect(screen.getByTestId('status-badge').querySelector('svg')).not.toBeNull();
    expect(screen.getByTestId('status-badge')).toHaveTextContent('Closed');
  });
});

describe('RootLayout shared UI', () => {
  it('shows SIMULATED only for an active simulated incident', () => {
    const { unmount } = render(<RootLayout incidents={[{ status: 'active', simulation_flag: true }]} />);
    expect(screen.getByText('SIMULATED')).toBeInTheDocument();
    unmount();
    render(<RootLayout incidents={[{ status: 'closed', simulation_flag: true }]} />);
    expect(screen.queryByText('SIMULATED')).toBeNull();
  });
  it('911 guidance is one click away and claims no dispatch', async () => {
    render(<RootLayout />);
    await userEvent.click(screen.getByRole('button', { name: /call 911/i }));
    expect(screen.getByTestId('emergency-911-body')).toHaveTextContent(/does not call, dispatch, or notify 911/i);
  });
});

