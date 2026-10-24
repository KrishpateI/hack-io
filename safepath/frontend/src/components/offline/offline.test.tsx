import { act, cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Legend } from '../../maps/layers/Legend';
import { ApiError } from '../../services/api';
import { memoryStore } from '../../services/idb';
import { OfflineManager, setOfflineManagerForTests, type QueueItem } from '../../services/offline';
import { CheckInPanel } from '../community/CheckInPanel';
import { MessageChannel } from '../community/MessageChannel';
import { ReportForm } from '../community/ReportForm';
import { OfflineStatus } from './OfflineStatus';

const SYNC = new Date('2026-03-04T10:15:00').getTime();

function setup(submit: (i: QueueItem) => Promise<unknown> = async () => undefined) {
  const m = new OfflineManager({
    storage: memoryStore(),
    submit,
    navigatorOnline: () => true,
    schedule: () => () => undefined,
    now: () => SYNC,
  });
  setOfflineManagerForTests(m);
  return m;
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => {
  cleanup();
  setOfflineManagerForTests(null);
});

describe('offline banner (Req 41.2, 41.3)', () => {
  it('is hidden online and shown persistently, with last sync time and the outdated warning, offline', async () => {
    const m = setup();
    await m.ready;
    m.noteSync(SYNC);
    render(<OfflineStatus />);
    expect(screen.queryByTestId('offline-banner')).toBeNull();
    act(() => m.setNavigatorOnline(false));
    const banner = screen.getByTestId('offline-banner');
    expect(banner).toHaveTextContent('You are offline.');
    expect(banner).toHaveTextContent(`Last synchronized at ${new Date(SYNC).toLocaleString()}`);
    expect(banner).toHaveTextContent('Conditions shown may be out of date.');
    expect(banner).toHaveTextContent(`Cached as of ${new Date(SYNC).toLocaleString()} \u2014 may be outdated`);
    expect(within(banner).queryByRole('button')).toBeNull(); // no way to dismiss it
  });

  it('adds the cached-data note to the map legend', async () => {
    const m = setup();
    await m.ready;
    m.noteSync(SYNC);
    render(<Legend visible={{}} />);
    expect(screen.queryByTestId('legend-cached')).toBeNull();
    act(() => m.noteLayer('layer-a', SYNC, true));
    expect(screen.getByTestId('legend-cached')).toHaveTextContent(`Cached map data, last synced ${new Date(SYNC).toLocaleString()}`);
  });
});

describe('queue item wording (Req 41.4, 41.5, 41.7)', () => {
  it('shows queued, then sent only after the server accepts', async () => {
    const m = setup();
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    act(() => void m.queueItem('report', { incident_id: 'i1', category: 'flooding', description: 'water' }));
    render(<OfflineStatus />);
    const row = screen.getByTestId('queue-item');
    expect(within(row).getByTestId('queue-item-status')).toHaveTextContent('Queued on this device \u2014 not sent');
    expect(row).toHaveTextContent('Other users and emergency services have not seen it');
    expect(row).not.toHaveTextContent(/Sent/);
    await act(async () => {
      m.setNavigatorOnline(true);
      await m.flush();
    });
    expect(screen.getByTestId('queue-item-status')).toHaveTextContent('Sent \u2014 visible to other users');
  });

  it('shows the server reason for a rejected item with Edit and Discard, and re-queues an edit', async () => {
    let reject = true;
    const m = setup(async () => {
      if (reject) throw new ApiError(422, 'validation_error', 'Description is too long');
    });
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    act(() => void m.queueItem('report', { incident_id: 'i1', category: 'flooding', description: 'x' }));
    await act(async () => {
      m.setNavigatorOnline(true);
      await m.flush();
    });
    render(<OfflineStatus />);
    expect(screen.getByTestId('queue-item-status')).toHaveTextContent('Rejected by server: Description is too long');
    reject = false;
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    const box = screen.getByLabelText('Description');
    await user.clear(box);
    await user.type(box, 'short');
    await user.click(screen.getByRole('button', { name: 'Save and queue again' }));
    await act(async () => {
      await m.flush();
    });
    expect(screen.getByTestId('queue-item-status')).toHaveTextContent('Sent');
  });

  it('can discard a queued item', async () => {
    const m = setup();
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    act(() => void m.queueItem('message', { incident_id: 'i1', message_type: 'update', content: 'hi' }));
    render(<OfflineStatus />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByTestId('queue-item')).toBeNull();
  });
});

describe('forms queue instead of sending while offline', () => {
  it('ReportForm saves the report on this device and does not claim it was submitted', async () => {
    const m = setup();
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    const submitReport = vi.fn();
    const user = userEvent.setup();
    render(<ReportForm incidentId="i1" indoorFeatureId="exit-1" submitReport={submitReport} />);
    await user.selectOptions(screen.getByLabelText('Category'), 'flooding');
    await user.click(screen.getByRole('button', { name: 'Submit report' }));
    expect(submitReport).not.toHaveBeenCalled();
    expect(screen.getByText(/Saved on this device and not sent/)).toBeInTheDocument();
    expect(screen.queryByText(/Report submitted/)).toBeNull();
    const [item] = m.getSnapshot().queue.items;
    expect(item.kind).toBe('report');
    expect(typeof item.body.client_report_id).toBe('string');
  });

  it('ReportForm shows the queue-full message at 50 items', async () => {
    const m = setup();
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    for (let i = 0; i < 50; i++) m.queueItem('report', { incident_id: 'i1', category: 'other', client_report_id: `c${i}` });
    const user = userEvent.setup();
    render(<ReportForm incidentId="i1" indoorFeatureId="exit-1" />);
    await user.selectOptions(screen.getByLabelText('Category'), 'flooding');
    await user.click(screen.getByRole('button', { name: 'Submit report' }));
    expect(screen.getByRole('alert')).toHaveTextContent('offline queue is full');
    expect(m.getSnapshot().pending).toBe(50);
  });

  it('CheckInPanel queues and says it was not sent, not "recorded"', async () => {
    const m = setup();
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    const user = userEvent.setup();
    render(<CheckInPanel incidentId="i1" />);
    await user.click(screen.getByLabelText("I'm safe"));
    await user.click(screen.getByRole('button', { name: 'Check in' }));
    expect(screen.getByText(/Saved on this device and not sent/)).toBeInTheDocument();
    expect(screen.queryByText('Check-in recorded.')).toBeNull();
    expect(m.getSnapshot().queue.items[0].kind).toBe('checkin');
  });

  it('MessageChannel queues a message while offline', async () => {
    const m = setup();
    await m.ready;
    act(() => m.setNavigatorOnline(false));
    const user = userEvent.setup();
    render(<MessageChannel incidentId="i1" messages={[]} />);
    await user.type(screen.getByLabelText('Message'), 'road blocked');
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByText(/Saved on this device and not sent/)).toBeInTheDocument();
    expect(m.getSnapshot().queue.items[0].body).toMatchObject({ content: 'road blocked', message_type: 'update' });
  });
});
