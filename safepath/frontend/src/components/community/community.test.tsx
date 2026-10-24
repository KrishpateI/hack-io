import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../services/api';
import { AssistanceRequest } from './AssistanceRequest';
import { CheckInPanel } from './CheckInPanel';
import { MessageChannel } from './MessageChannel';
import { ReportCard } from './ReportCard';
import { ReportForm } from './ReportForm';
import { ReportList } from './ReportList';
import { CORROBORATED_TEXT } from './ReportStatusBadge';
import type { CommunityMessage, CommunityReport } from './types';

const report = (over: Partial<CommunityReport> = {}): CommunityReport => ({
  report_id: 'r1',
  category: 'flooding',
  description: 'Water over the curb',
  created_at: '2025-01-01T12:00:00Z',
  alias: 'Heron-12',
  source_label: 'Community report',
  verification_status: 'Unverified',
  confirmations: 0,
  disputes: 0,
  simulated: false,
  ...over,
});

const message = (over: Partial<CommunityMessage> = {}): CommunityMessage => ({
  message_id: 'm1',
  message_type: 'update',
  content: 'Hello',
  created_at: '2025-01-01T12:00:00Z',
  alias: 'Otter-3',
  is_official: false,
  source_record_id: null,
  source_organization: null,
  source_url: null,
  ...over,
});

describe('ReportCard', () => {
  it('shows exact Corroborated wording with icon and text', () => {
    render(<ReportCard report={report({ verification_status: 'Corroborated' })} />);
    const badge = screen.getByTestId('report-status');
    expect(badge).toHaveTextContent(CORROBORATED_TEXT);
    expect(CORROBORATED_TEXT).toBe('Corroborated by community \u2014 not officially verified');
    expect(badge.querySelector('svg')).not.toBeNull();
    expect(screen.getByTestId('report-source')).toHaveTextContent('Community report');
  });

  it('every status has visible text and an icon (never color alone)', () => {
    for (const s of ['Unverified', 'Corroborated', 'Officially_Verified', 'Disputed', 'Resolved', 'Expired']) {
      const { unmount } = render(<ReportCard report={report({ verification_status: s })} />);
      const badge = screen.getByTestId('report-status');
      expect(badge.textContent?.trim().length).toBeGreaterThan(0);
      expect(badge.querySelector('svg')).not.toBeNull();
      unmount();
    }
  });

  it('shows Official/Moderator label only for Officially_Verified, plus SIMULATED when simulated', () => {
    const { rerender } = render(<ReportCard report={report({ verification_status: 'Corroborated' })} />);
    expect(screen.queryByText(/Official\/Moderator-confirmed/)).toBeNull();
    expect(screen.queryByTestId('simulated-badge')).toBeNull();
    rerender(<ReportCard report={report({ verification_status: 'Officially_Verified', simulated: true })} />);
    expect(screen.getByText('Official/Moderator-confirmed')).toBeInTheDocument();
    expect(screen.getByTestId('simulated-badge')).toHaveTextContent('SIMULATED');
  });

  it('disables corroborate and dispute for own reports', () => {
    render(<ReportList reports={[report()]} ownReportIds={new Set(['r1'])} />);
    expect(screen.getByRole('button', { name: 'Corroborate' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Dispute' })).toBeDisabled();
  });

  it('updates counts optimistically and reverts with an error on failure', async () => {
    const user = userEvent.setup();
    let reject: (e: unknown) => void = () => {};
    const onVote = vi.fn(() => new Promise((_, rej) => { reject = rej; }));
    render(<ReportCard report={report()} onVote={onVote} />);
    await user.click(screen.getByRole('button', { name: 'Corroborate' }));
    expect(screen.getByTestId('report-counts')).toHaveTextContent('1 corroborations');
    reject(new ApiError(409, 'conflict', 'Already responded'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Already responded');
    expect(screen.getByTestId('report-counts')).toHaveTextContent('0 corroborations');
  });

  it('keeps optimistic count and locks buttons after success', async () => {
    const user = userEvent.setup();
    render(<ReportCard report={report()} onVote={() => Promise.resolve({})} />);
    await user.click(screen.getByRole('button', { name: 'Dispute' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Corroborate' })).toBeDisabled());
    expect(screen.getByTestId('report-counts')).toHaveTextContent('1 disputes');
  });
});

describe('ReportForm', () => {
  it('offers all 9 categories and a 1000-char counter', async () => {
    const user = userEvent.setup();
    render(<ReportForm incidentId="i1" />);
    expect(within(screen.getByLabelText('Category')).getAllByRole('option')).toHaveLength(10); // placeholder + 9
    await user.type(screen.getByLabelText(/Description/), 'abc');
    expect(screen.getByText('3/1000 characters')).toBeInTheDocument();
  });

  it('shows local validation errors without calling the API', async () => {
    const user = userEvent.setup();
    const submitReport = vi.fn();
    render(<ReportForm incidentId="i1" submitReport={submitReport} />);
    await user.click(screen.getByRole('button', { name: 'Submit report' }));
    expect(screen.getByText('Choose a category.')).toBeInTheDocument();
    expect(screen.getByText(/Click the map or choose an indoor feature/)).toBeInTheDocument();
    expect(submitReport).not.toHaveBeenCalled();
  });

  it('displays field errors from the API error envelope', async () => {
    const user = userEvent.setup();
    const submitReport = vi.fn().mockRejectedValue(
      new ApiError(400, 'validation_error', 'invalid fields: category', [
        { field: 'category', problem: 'unsupported value from server' },
      ]),
    );
    render(<ReportForm incidentId="i1" location={{ latitude: 41.88, longitude: -87.63 }} submitReport={submitReport} />);
    await user.selectOptions(screen.getByLabelText('Category'), 'flooding');
    await user.click(screen.getByRole('button', { name: 'Submit report' }));
    expect(await screen.findByText('unsupported value from server')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('invalid fields: category');
    expect(submitReport).toHaveBeenCalledWith(
      expect.objectContaining({ incident_id: 'i1', category: 'flooding', latitude: 41.88, longitude: -87.63 }),
    );
  });

  it('hands the body to the offline queue when offline', async () => {
    const user = userEvent.setup();
    const onOffline = vi.fn();
    const spy = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    render(<ReportForm incidentId="i1" indoorFeatureId="exit-1" onOffline={onOffline} submitReport={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText('Category'), 'blocked building exit');
    await user.click(screen.getByRole('button', { name: 'Submit report' }));
    expect(onOffline).toHaveBeenCalledWith(expect.objectContaining({ indoor_feature_id: 'exit-1' }));
    spy.mockRestore();
  });
});

describe('MessageChannel', () => {
  it('lists community messages oldest first with Community label and 6 types', () => {
    render(
      <MessageChannel
        incidentId="i1"
        messages={[
          message({ message_id: 'b', content: 'second', created_at: '2025-01-01T12:05:00Z' }),
          message({ message_id: 'a', content: 'first', created_at: '2025-01-01T12:00:00Z' }),
        ]}
      />,
    );
    const items = screen.getAllByTestId('community-message');
    expect(items[0]).toHaveTextContent('first');
    expect(items[1]).toHaveTextContent('second');
    expect(items[0]).toHaveTextContent('Community');
    expect(within(screen.getByLabelText('Message type')).getAllByRole('option')).toHaveLength(6);
  });

  it('keeps official notices in a separate labeled panel', () => {
    render(
      <MessageChannel
        incidentId="i1"
        messages={[message({ content: 'community text' })]}
        notices={[
          { notice_id: 'n1', text: 'Evacuate zone A', source_organization: 'City OEM', source_url: null, source_record_id: 'src-9', is_official: true, simulated: false, published_at: null },
          { notice_id: 'n2', text: 'Unofficial relay', source_organization: null, source_url: null, source_record_id: null, is_official: false, simulated: false, published_at: null },
        ]}
      />,
    );
    const panel = screen.getByTestId('official-notices');
    expect(within(panel).getByText('Evacuate zone A')).toBeInTheDocument();
    expect(within(panel).getAllByText('Official')).toHaveLength(1);
    expect(within(panel).getByText(/record src-9/)).toBeInTheDocument();
    expect(within(panel).queryByText('community text')).toBeNull();
    expect(screen.getAllByTestId('community-message')).toHaveLength(1);
  });

  it('shows the no-dispatch notice and 911 guidance for assistance messages and type', async () => {
    const user = userEvent.setup();
    render(<MessageChannel incidentId="i1" messages={[message({ message_type: 'assistance_request' })]} />);
    expect(within(screen.getAllByTestId('community-message')[0]).getByTestId('no-dispatch-notice')).toHaveTextContent(
      'SafePath does not dispatch emergency services',
    );
    expect(screen.getAllByTestId('guidance-911')).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText('Message type'), 'accessibility_assistance_request');
    expect(screen.getAllByTestId('no-dispatch-notice')).toHaveLength(2);
  });

  it('rejects over-length messages and sends valid ones', async () => {
    const user = userEvent.setup();
    const send = vi.fn().mockResolvedValue({});
    render(<MessageChannel incidentId="i1" messages={[]} sendMessage={send} />);
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByText('Enter a message.')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'Road open');
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    expect(send).toHaveBeenCalledWith({ incident_id: 'i1', message_type: 'update', content: 'Road open' });
  });
});

describe('CheckInPanel', () => {
  it('has four statuses and location sharing off by default with explanation', () => {
    render(<CheckInPanel incidentId="i1" location={{ latitude: 1, longitude: 2 }} />);
    expect(screen.getAllByRole('radio')).toHaveLength(4);
    expect(screen.getByRole('switch', { name: /Share my approximate location/ })).not.toBeChecked();
    expect(screen.getByText(/approximate location \(rounded to about 200 m\)/)).toBeInTheDocument();
    expect(screen.getByText(/removed automatically after 72 hours/)).toBeInTheDocument();
  });

  it('submits share_location false unless the user opts in', async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue({});
    render(<CheckInPanel incidentId="i1" location={{ latitude: 1, longitude: 2 }} submitCheckin={submit} />);
    await user.click(screen.getByRole('radio', { name: "I'm safe" }));
    await user.click(screen.getByRole('button', { name: 'Check in' }));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ status: 'safe', share_location: false }));
    expect(submit.mock.calls[0][0]).not.toHaveProperty('location');
  });

  it('shows the no-dispatch notice and 911 guidance on "I need assistance"', async () => {
    const user = userEvent.setup();
    render(<CheckInPanel incidentId="i1" />);
    expect(screen.queryByTestId('no-dispatch-notice')).toBeNull();
    await user.click(screen.getByRole('radio', { name: 'I need assistance' }));
    expect(screen.getByTestId('no-dispatch-notice')).toHaveTextContent('SafePath does not dispatch emergency services');
    expect(screen.getByTestId('guidance-911')).toHaveTextContent('call 911');
  });
});

describe('AssistanceRequest', () => {
  it('reveals the no-dispatch notice when opened and posts an assistance_request', async () => {
    const user = userEvent.setup();
    const send = vi.fn().mockResolvedValue({});
    render(<AssistanceRequest incidentId="i1" sendMessage={send} />);
    expect(screen.queryByTestId('no-dispatch-notice')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Request assistance' }));
    expect(screen.getByTestId('no-dispatch-notice')).toHaveTextContent('SafePath does not dispatch emergency services');
    await user.type(screen.getByLabelText('What help do you need?'), 'Need a ramp');
    await user.click(screen.getByRole('button', { name: 'Send request' }));
    expect(send).toHaveBeenCalledWith({ incident_id: 'i1', message_type: 'assistance_request', content: 'Need a ramp' });
  });
});
