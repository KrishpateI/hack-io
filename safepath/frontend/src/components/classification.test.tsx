import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import { classify, labelAttribute, type SourceLabelKind } from '../services/labels';
import { RootLayout } from './RootLayout';
import { SourceLabel } from './SourceLabel';
import { DISCLAIMER_TEXT } from '../styles/tokens';

const EXACT =
  'SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies.';
const ALL_LABELS: SourceLabelKind[] = ['Official', 'Simulated', 'Community', 'Unknown source'];

const itemArb = fc.record({
  source_record: fc.option(
    fc.record({
      is_official: fc.option(fc.boolean(), { nil: undefined }),
      source_id: fc.option(fc.string(), { nil: null }),
    }),
    { nil: null },
  ),
  simulation_flag: fc.option(fc.boolean(), { nil: null }),
  origin: fc.option(fc.constantFrom('report', 'message', 'other', ''), { nil: null }),
});

describe('Property 33: Content classification and labeling', () => {
  // Feature: safepath-evacuation-navigation, Property 33: Content classification and labeling
  // **Validates: Requirements 10.4, 10.5, 36.5, 1.6**
  it('classifies per spec precedence and renders exactly one label as text', () => {
    fc.assert(
      fc.property(itemArb, (item) => {
        const official = item.source_record?.is_official === true;
        const expected: SourceLabelKind = official
          ? 'Official'
          : item.simulation_flag === true
            ? 'Simulated'
            : item.origin === 'report' || item.origin === 'message'
              ? 'Community'
              : 'Unknown source';
        expect(classify(item)).toBe(expected);

        const { unmount } = render(<SourceLabel item={item} />);
        const el = screen.getByTestId('source-label');
        const shown = ALL_LABELS.filter((l) => el.textContent!.includes(l));
        unmount();
        return shown.length === 1 && shown[0] === expected;
      }),
      { numRuns: 100 },
    );
  });

  // Feature: safepath-evacuation-navigation, Property 33: Content classification and labeling
  it('renders any attribute without data as Unknown', () => {
    fc.assert(
      fc.property(fc.constantFrom(undefined, null, '', ' ', '\t\n'), (v) => labelAttribute(v) === 'Unknown'),
      { numRuns: 100 },
    );
  });
});

describe.each([320, 2560])('shared UI at width %i', (width) => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
    window.dispatchEvent(new Event('resize'));
  });

  // Validates: Requirements 1.1, 35.4
  it('shows the verbatim disclaimer with no dismiss control', () => {
    render(<RootLayout />);
    const banner = screen.getByTestId('disclaimer-banner');
    expect(DISCLAIMER_TEXT).toBe(EXACT);
    expect(banner.textContent).toBe(EXACT);
    expect(banner).toBeVisible();
    expect(within(banner).queryByRole('button')).toBeNull();
    expect(screen.queryByRole('button', { name: /dismiss|close|hide/i })).toBeNull();
  });

  // Validates: Requirements 1.3, 10.8
  it('reaches 911 guidance in one interaction', async () => {
    render(<RootLayout />);
    await userEvent.click(screen.getByRole('button', { name: /emergency: call 911/i }));
    expect(screen.getByTestId('emergency-911-body')).toHaveTextContent(/call 911/i);
  });
});

describe('SIMULATED header indicator', () => {
  // Validates: Requirements 10.4, 36.5
  it('appears for an active simulated incident and not otherwise', () => {
    const { unmount } = render(<RootLayout incidents={[{ status: 'active', simulation_flag: true }]} />);
    expect(within(screen.getByRole('banner')).getByText('SIMULATED')).toBeInTheDocument();
    unmount();
    render(<RootLayout incidents={[{ status: 'active', simulation_flag: false }]} />);
    expect(screen.queryByText('SIMULATED')).toBeNull();
  });
});
