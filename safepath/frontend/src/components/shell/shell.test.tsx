import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CommunityCommunicationsScreen } from '../../pages/CommunityCommunicationsScreen';
import { DisasterSelectionScreen } from '../../pages/DisasterSelectionScreen';
import { IncidentOverviewScreen, countByStatus } from '../../pages/IncidentOverviewScreen';
import { OfficialGuidanceScreen } from '../../pages/OfficialGuidanceScreen';
import { SafetyCheckInScreen } from '../../pages/SafetyCheckInScreen';
import type { Incident } from '../../services/api';
import type { CommunityMessage, CommunityReport } from '../community/types';
import { BottomControls } from './BottomControls';
import { RightSidebarView, type CommunityFeed } from './RightSidebar';
import { SCREEN_IDS, SCREEN_TITLES, screenFromHash, screenHash } from './screens';
import { ScreenFrame } from './ScreenFrame';

const report: CommunityReport = {
  report_id: 'r1', category: 'flooding', description: 'Water over the curb', created_at: '2025-01-01T12:00:00Z',
  alias: 'Heron-12', source_label: 'Community report', verification_status: 'Unverified', confirmations: 0, disputes: 0, simulated: false,
};
const msg = (over: Partial<CommunityMessage>): CommunityMessage => ({
  message_id: 'm1', message_type: 'update', content: 'Neighbors are gathering', created_at: '2025-01-01T12:00:00Z', alias: 'Otter-3',
  is_official: false, source_record_id: null, source_organization: null, source_url: null, ...over,
});
const feed: CommunityFeed = {
  reports: [report],
  messages: [
    msg({}),
    msg({ message_id: 'm2', content: 'Please help on 5th floor', message_type: 'assistance_request' }),
    msg({ message_id: 'm3', content: 'Road closed at Main', is_official: true, source_record_id: 'SR-1', source_organization: 'Chicago OEMC' }),
  ],
  checkins: [{ checkin_id: 'c1', status: 'safe', status_label: null, created_at: null, alias: 'Fox-1' }],
};
const incident: Incident = {
  incident_id: 'flood-1', incident_type: 'flood', simulation_flag: true, status: 'active', source: 'demo',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

describe('screens registry', () => {
  it('has a title for every id and round-trips hashes', () => {
    for (const id of SCREEN_IDS) {
      expect(SCREEN_TITLES[id]).toBeTruthy();
      expect(screenFromHash(screenHash(id))).toBe(id);
    }
    expect(screenFromHash('#/nope')).toBeNull();
    expect(screenFromHash('')).toBeNull();
  });
});

describe('RightSidebar', () => {
  it('switches tabs and keeps official notices apart from community content', async () => {
    const user = userEvent.setup();
    render(<RightSidebarView incidentId="flood-1" feed={feed} />);
    expect(screen.getByRole('tab', { name: /Reports/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Water over the curb')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /Messages & notices/ }));
    const official = screen.getByTestId('official-notices');
    expect(within(official).getByText('Road closed at Main')).toBeInTheDocument();
    expect(within(official).getByText(/Chicago OEMC/)).toBeInTheDocument();
    expect(screen.getByText('Neighbors are gathering')).toBeInTheDocument();
    expect(official).not.toContainElement(screen.getByText('Neighbors are gathering'));

    await user.click(screen.getByRole('tab', { name: /Check-ins & assistance/ }));
    const assist = screen.getByTestId('assistance-requests');
    expect(within(assist).getByText('Please help on 5th floor')).toBeInTheDocument();
    expect(assist).toHaveTextContent(/not sent to emergency services/);
    expect(screen.getByRole('status')).toHaveTextContent('1 community reports');
  });

  it('supports arrow-key tab navigation', async () => {
    const user = userEvent.setup();
    render(<RightSidebarView incidentId="flood-1" feed={feed} />);
    screen.getByRole('tab', { name: /Reports/ }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: /Messages & notices/ })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('BottomControls', () => {
  it('calls the matching handler for each control', async () => {
    const user = userEvent.setup();
    const h = { onReportHazard: vi.fn(), onRequestAssistance: vi.fn(), onMarkSafe: vi.fn(), onViewGuidance: vi.fn(), onRecalculate: vi.fn() };
    render(<BottomControls {...h} />);
    await user.click(screen.getByRole('button', { name: 'Report a hazard' }));
    await user.click(screen.getByRole('button', { name: 'Request assistance' }));
    await user.click(screen.getByRole('button', { name: 'Mark safe' }));
    await user.click(screen.getByRole('button', { name: 'View official guidance' }));
    await user.click(screen.getByRole('button', { name: 'Recalculate route' }));
    for (const fn of Object.values(h)) expect(fn).toHaveBeenCalledTimes(1);
  });

  it('disables recalculate while busy', () => {
    render(<BottomControls onReportHazard={vi.fn()} onRequestAssistance={vi.fn()} onMarkSafe={vi.fn()} onViewGuidance={vi.fn()} onRecalculate={vi.fn()} recalculating />);
    expect(screen.getByRole('button', { name: /Recalculating/ })).toBeDisabled();
  });
});

describe('ScreenFrame', () => {
  it('focuses the heading, closes on Escape and restores focus', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { rerender } = render(<button>opener</button>);
    screen.getByText('opener').focus();
    rerender(<><button>opener</button><ScreenFrame title="Demo" onClose={onClose}><p>body</p></ScreenFrame></>);
    expect(screen.getByRole('heading', { name: 'Demo' })).toHaveFocus();
    expect(screen.getByRole('dialog', { name: 'Demo' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
    rerender(<button>opener</button>);
    expect(screen.getByText('opener')).toHaveFocus();
  });

  it('names the back control "Back to <title>" and calls onBack', async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();
    render(<ScreenFrame title="Demo" onClose={vi.fn()} backTitle="Route" onBack={onBack}><p>body</p></ScreenFrame>);
    await user.click(screen.getByRole('button', { name: 'Back to Route' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('omits the back control without backTitle', () => {
    render(<ScreenFrame title="Demo" onClose={vi.fn()}><p>body</p></ScreenFrame>);
    expect(screen.queryByRole('button', { name: /^Back to/ })).not.toBeInTheDocument();
  });
});

describe('screens', () => {
  it('SafetyCheckIn shows separate 911 guidance and never claims dispatch', () => {
    render(<SafetyCheckInScreen incidentId="flood-1" onClose={vi.fn()} />);
    const box = screen.getByTestId('emergency-contact-guidance');
    expect(box).toHaveTextContent(/call 911/);
    expect(box).toHaveTextContent(/does not dispatch/);
    expect(box).not.toContainElement(screen.getByRole('button', { name: /safe/i }));
    expect(screen.getByRole('heading', { name: 'Privacy' })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/(we|safepath) (will )?(dispatch|notify|contact) (911|emergency)/i);
  });

  it('IncidentOverview shows counts, restrictions, notices and the historical label', () => {
    render(
      <IncidentOverviewScreen
        incident={incident}
        reports={[report, { ...report, report_id: 'r2', verification_status: 'Disputed' }]}
        notices={[{ notice_id: 'n1', text: 'Closure', source_organization: 'OEMC', source_url: null, source_record_id: 'SR-1', is_official: true, simulated: false, published_at: null }]}
        activeRestrictionCount={3}
        showCbdReference
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId('restriction-count')).toHaveTextContent('3');
    expect(screen.getByTestId('report-counts')).toHaveTextContent('Disputed');
    expect(screen.getByTestId('simulated-badge')).toBeInTheDocument();
    expect(screen.getByText(/record SR-1/)).toBeInTheDocument();
    expect(screen.getByTestId('historical-label')).toBeInTheDocument();
    expect(countByStatus([report]).Unverified).toBe(1);
  });

  it('OfficialGuidance says it is not a live order and lists sources', () => {
    render(<OfficialGuidanceScreen onClose={vi.fn()} />);
    expect(screen.getAllByText(/not a live order/i).length).toBeGreaterThan(0);
    expect(screen.getByTestId('official-links')).toBeInTheDocument();
    expect(screen.getAllByText(/Retrieved/).length).toBeGreaterThan(0);
  });

  it('DisasterSelection labels scenarios SIMULATED and selects a type', async () => {
    const user = userEvent.setup();
    const onSelectType = vi.fn();
    render(<DisasterSelectionScreen selectedType="flood" incidents={[incident]} selectedIncidentId={null} onSelectType={onSelectType} onSelectIncident={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getAllByTestId('simulated-badge').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: /Earthquake/ }));
    expect(onSelectType).toHaveBeenCalledWith('earthquake');
  });

  it('CommunityCommunications separates official notices from community content', () => {
    render(<CommunityCommunicationsScreen incidentId="flood-1" feed={feed} onClose={vi.fn()} />);
    expect(screen.getByTestId('official-notices')).toHaveTextContent('Road closed at Main');
    expect(screen.getByTestId('official-notices')).not.toHaveTextContent('Neighbors are gathering');
  });
});
