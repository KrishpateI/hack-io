import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SafetyTab } from './SafetyTab';

function setup(hazard?: string | null) {
  const cbs = {
    onReportHazard: vi.fn(),
    onRequestAssistance: vi.fn(),
    onMarkSafe: vi.fn(),
    onViewGuidance: vi.fn(),
  };
  render(<SafetyTab {...cbs} hazard={hazard} />);
  return cbs;
}

describe('SafetyTab', () => {
  it('renders the four rows first and calls the matching callbacks', async () => {
    const cbs = setup();
    const names = ['Report a hazard', 'Request assistance', 'Mark safe', 'View official guidance'];
    const buttons = screen.getAllByRole('button');
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(names);
    await userEvent.click(screen.getByRole('button', { name: 'Report a hazard' }));
    await userEvent.click(screen.getByRole('button', { name: 'Request assistance' }));
    await userEvent.click(screen.getByRole('button', { name: 'Mark safe' }));
    await userEvent.click(screen.getByRole('button', { name: 'View official guidance' }));
    expect(cbs.onReportHazard).toHaveBeenCalledTimes(1);
    expect(cbs.onRequestAssistance).toHaveBeenCalledTimes(1);
    expect(cbs.onMarkSafe).toHaveBeenCalledTimes(1);
    expect(cbs.onViewGuidance).toHaveBeenCalledTimes(1);
  });

  it('shows the 911 guidance in its own Card, outside the rows list', () => {
    setup();
    const card = screen.getByTestId('safety-911-card');
    expect(card).toHaveTextContent('call 911 from your phone');
    expect(card).toHaveTextContent('does not call, dispatch, or notify 911');
    expect(card.closest('ul')).toBeNull();
    expect(card.querySelector('button, a')).toBeNull();
  });

  it('renders official guidance after the 911 card', () => {
    setup();
    const card = screen.getByTestId('safety-911-card');
    const guidance = screen.getByTestId('official-guidance');
    expect(card.compareDocumentPosition(guidance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
