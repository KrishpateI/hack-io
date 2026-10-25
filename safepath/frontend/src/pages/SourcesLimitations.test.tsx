import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { HISTORICAL_TEXT, HistoricalLabel } from '../components/HistoricalLabel';
import { OFFICIAL_LINKS, OfficialLinks } from '../components/OfficialLinks';
import { SourceRecordPopup } from '../components/SourceRecordPopup';
import type { SourceRecord } from '../services/sources';
import SourcesLimitations from './SourcesLimitations';

const rec: SourceRecord = {
  source_id: 'cbd',
  organization: 'City of Chicago OEMC',
  url: 'https://example.org/cbd.pdf',
  title: 'CBD Plan',
  published_at: '2007-01-01',
  retrieved_at: '2025-01-02T03:04:05Z',
  data_classification: 'Historical',
  verification_status: 'verified',
  page_or_section: 'p. 12',
  is_official_source: true,
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

const stubFetch = (impl: () => Promise<unknown>) => vi.stubGlobal('fetch', vi.fn(impl));

describe('SourceRecordPopup', () => {
  it('shows all provenance fields', () => {
    render(<SourceRecordPopup record={rec} />);
    const p = screen.getByTestId('source-popup');
    for (const t of ['City of Chicago OEMC', 'https://example.org/cbd.pdf', 'CBD Plan', '2007-01-01', '2025-01-02 03:04:05 UTC', 'Historical', 'verified', 'p. 12']) {
      expect(p).toHaveTextContent(t);
    }
    expect(screen.getByTestId('historical-label')).toHaveTextContent(HISTORICAL_TEXT);
  });
  it('renders Unknown for missing record and fields', () => {
    const { unmount } = render(<SourceRecordPopup record={null} />);
    expect(screen.getByTestId('source-popup')).toHaveTextContent('Unknown source');
    unmount();
    render(<SourceRecordPopup record={{ ...rec, published_at: null, page_or_section: null }} />);
    expect(screen.getByTestId('source-popup')).toHaveTextContent('Unknown');
  });
});

describe('official links and historical label', () => {
  it('links to the Req 9.1 URLs', () => {
    render(<OfficialLinks />);
    expect(OFFICIAL_LINKS.map((l) => l.url)).toEqual([
      'https://www.chicago.gov/city/en/depts/oem.html',
      'https://ready.illinois.gov/',
      'https://ready.uic.edu/',
    ]);
    for (const l of OFFICIAL_LINKS) {
      expect(screen.getByRole('link', { name: new RegExp(l.name) })).toHaveAttribute('href', l.url);
    }
  });
  it('historical label has the exact text', () => {
    render(<HistoricalLabel />);
    expect(screen.getByTestId('historical-label').textContent).toBe('Historical reference (2007), not a current evacuation order');
  });
});

describe('SourcesLimitations page', () => {
  it('lists sources, opens popup, and shows limitations', async () => {
    stubFetch(async () => ({ ok: true, json: async () => [rec] }));
    render(<SourcesLimitations />);
    await userEvent.click(await screen.findByRole('button', { name: 'CBD Plan' }));
    expect(screen.getByTestId('source-popup')).toHaveTextContent('p. 12');
    expect(screen.getByTestId('limitations-list').children.length).toBeGreaterThan(3);
  });
  it('passes filters to the API', async () => {
    const f = vi.fn(async () => ({ ok: true, json: async () => [] }));
    vi.stubGlobal('fetch', f);
    render(<SourcesLimitations />);
    expect(await screen.findByText('No sources match these filters.')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Classification'), 'Historical');
    await waitFor(() => expect(String((f.mock.calls.at(-1) as unknown[])[0])).toContain('data_classification=Historical'));
  });
  it('shows an error with limitations still available', async () => {
    stubFetch(async () => {
      throw new TypeError('offline');
    });
    render(<SourcesLimitations />);
    expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
    expect(screen.getByTestId('limitations-list')).toBeInTheDocument();
  });
});

describe('App navigation', () => {
  it('reaches the Sources screen via hash link', async () => {
    stubFetch(async () => ({ ok: true, json: async () => [] }));
    render(<App />);
    // The row now lives on the More tab (the old "Screens" nav was retired).
    const primary = await screen.findByRole('navigation', { name: 'Primary' });
    await userEvent.click(within(primary).getByRole('button', { name: /^More\b/ }));
    await userEvent.click(await screen.findByRole('link', { name: /data sources and limitations/i }));
    expect(await screen.findByRole('heading', { name: /data sources and limitations/i, level: 2 })).toBeInTheDocument();
  });
});
