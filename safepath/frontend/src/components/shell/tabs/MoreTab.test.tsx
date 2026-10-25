import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MoreTab, type MoreTabProps } from './MoreTab';

const me = {
  role: 'resident',
  display_name: 'Sam',
  is_guest: true,
  expires_at: null,
} as unknown as MoreTabProps['me'];

function setup(over: Partial<MoreTabProps> = {}) {
  const props: MoreTabProps = {
    me,
    appearanceSetting: 'system',
    onAppearanceChange: vi.fn(),
    shortcutsEnabled: true,
    onShortcutsChange: vi.fn(),
    ...over,
  };
  render(<MoreTab {...props} />);
  return props;
}

describe('MoreTab', () => {
  it('renders the three screen rows as links and hides Demo console by default', () => {
    setup();
    expect(screen.getByRole('link', { name: 'Incident overview' })).toHaveAttribute('href', '#/incident-overview');
    expect(screen.getByRole('link', { name: 'Select a disaster' })).toHaveAttribute('href', '#/disaster-selection');
    expect(screen.getByRole('link', { name: 'Data sources and limitations' })).toHaveAttribute('href', '#/data-sources');
    expect(screen.queryByRole('link', { name: 'Demo console' })).toBeNull();
  });

  it('shows Demo console only in demo mode', () => {
    setup({ demoMode: true });
    expect(screen.getByRole('link', { name: 'Demo console' })).toHaveAttribute('href', '#/demo');
  });

  it('renders official links and account details', () => {
    setup();
    expect(screen.getByTestId('official-links')).toBeInTheDocument();
    expect(screen.getByText('Guest')).toBeInTheDocument();
    expect(screen.getByText('Sam')).toBeInTheDocument();
  });

  it('changes appearance through the radio group', async () => {
    const p = setup();
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(p.onAppearanceChange).toHaveBeenCalledWith('dark');
  });

  it('toggles keyboard shortcuts and lists the sequence', async () => {
    const p = setup();
    await userEvent.click(screen.getByRole('switch', { name: 'Keyboard shortcuts' }));
    expect(p.onShortcutsChange).toHaveBeenCalledWith(false);
    const list = screen.getByTestId('shortcut-list');
    expect(list).toHaveTextContent('g then 1 to 5');
  });
});
