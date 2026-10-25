import { cleanup, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { TAB_IDS, TAB_TITLES, type TabId } from '../shell/navigation';
import { TabBar } from './TabBar';
import { TabList } from './TabList';

// Feature: ios-style-ui-redesign, Property 12: Navigation components expose the same five tabs
// Property 12: Navigation components expose the same five tabs.
// For any current tab, the Tab_List and the Tab_Bar each render five named entries in the order
// Map, Route, Community, Safety, More, each with an icon and a text label, and exactly one entry
// carries aria-current="page".
// The input space (five current tabs) is finite, so it is enumerated exhaustively.
// **Validates: Requirements 6.1, 6.2, 6.3, 12.4**

afterEach(cleanup);

const EXPECTED_LABELS = ['Map', 'Route', 'Community', 'Safety', 'More'];

const TABS = TAB_IDS.map((id) => ({ id, label: TAB_TITLES[id], icon: id }));

const noop = () => {};

const renderers = {
  TabList: (current: TabId) => render(<TabList tabs={TABS} current={current} onSelect={noop} />),
  'TabList (rail)': (current: TabId) =>
    render(<TabList tabs={TABS} current={current} onSelect={noop} rail />),
  TabBar: (current: TabId) => render(<TabBar tabs={TABS} current={current} onSelect={noop} />),
} as const;

describe('Property 12: Navigation components expose the same five tabs', () => {
  it('the tab data is exactly Map, Route, Community, Safety, More in order', () => {
    expect(TABS.map((t) => t.label)).toEqual(EXPECTED_LABELS);
  });

  describe.each(Object.keys(renderers) as (keyof typeof renderers)[])('%s', (name) => {
    it.each(TAB_IDS)('with current tab "%s": five ordered, named, iconed entries and one aria-current', (current) => {
      const { container } = renderers[name](current);
      const nav = within(container).getByRole('navigation', { name: 'Primary' });
      const buttons = within(nav).getAllByRole('button');

      // Five entries, in order, each with an accessible name (text label, even in rail mode).
      expect(buttons).toHaveLength(5);
      expect(buttons.map((b) => b.textContent?.trim())).toEqual(EXPECTED_LABELS);
      expect(buttons.map((b) => within(b).getByText(/\S/).textContent)).toEqual(EXPECTED_LABELS);

      // Each entry has an icon.
      for (const b of buttons) {
        expect(b.querySelector('svg')).not.toBeNull();
      }

      // Exactly one entry is current, and it is the requested tab.
      const currentButtons = buttons.filter((b) => b.getAttribute('aria-current') === 'page');
      expect(currentButtons).toHaveLength(1);
      expect(buttons.indexOf(currentButtons[0])).toBe(TAB_IDS.indexOf(current));
      expect(currentButtons[0].textContent?.trim()).toBe(TAB_TITLES[current]);
    });
  });
});
