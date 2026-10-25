import { useRef, useState } from 'react';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SegmentedControl } from './SegmentedControl';
import { ToggleSwitch } from './ToggleSwitch';
import { GroupedRow } from './GroupedList';
import { PageHeader } from './PageHeader';
import { useViewportTier, useReducedMotion, useSheetDrag, useDismissibleLayer } from './hooks';

/** Validates: Requirements 11.5, 13.2, 14.8, 15.4 */

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const OPTIONS = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Beta' },
  { value: 'c', label: 'Gamma' },
] as const;

function Seg({ variant, initial = 'a' }: { variant: 'tabs' | 'radio'; initial?: string }) {
  const [v, setV] = useState(initial);
  return <SegmentedControl variant={variant} options={OPTIONS} value={v} onChange={setV} label="Pick" />;
}

describe.each([
  ['tabs', 'tab', 'aria-selected'],
  ['radio', 'radio', 'aria-checked'],
] as const)('SegmentedControl (%s)', (variant, role, attr) => {
  it('marks only the selected segment and keeps it as the tab stop', () => {
    render(<Seg variant={variant} initial="b" />);
    const items = screen.getAllByRole(role);
    expect(items.map((i) => i.getAttribute(attr))).toEqual(['false', 'true', 'false']);
    expect(items.map((i) => i.tabIndex)).toEqual([-1, 0, -1]);
  });

  it('moves with arrow keys (wrapping), Home and End', async () => {
    const user = userEvent.setup();
    render(<Seg variant={variant} />);
    screen.getByRole(role, { name: 'Alpha' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole(role, { name: 'Beta' })).toHaveAttribute(attr, 'true');
    expect(screen.getByRole(role, { name: 'Beta' })).toHaveFocus();
    await user.keyboard('{End}');
    expect(screen.getByRole(role, { name: 'Gamma' })).toHaveAttribute(attr, 'true');
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole(role, { name: 'Alpha' })).toHaveAttribute(attr, 'true');
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole(role, { name: 'Gamma' })).toHaveAttribute(attr, 'true');
    await user.keyboard('{Home}');
    expect(screen.getByRole(role, { name: 'Alpha' })).toHaveAttribute(attr, 'true');
    expect(screen.getByRole(role, { name: 'Alpha' })).toHaveFocus();
  });
});

describe('ToggleSwitch', () => {
  function Toggle() {
    const [on, setOn] = useState(false);
    return <ToggleSwitch label="Alerts" checked={on} onChange={setOn} />;
  }

  it('shows On/Off text and toggles from the keyboard', async () => {
    const user = userEvent.setup();
    render(<Toggle />);
    const sw = screen.getByRole('switch', { name: 'Alerts' });
    expect(sw).not.toBeChecked();
    expect(screen.getByTestId('switch-state')).toHaveTextContent('Off');
    sw.focus();
    await user.keyboard(' ');
    expect(sw).toBeChecked();
    expect(screen.getByTestId('switch-state')).toHaveTextContent('On');
    await user.keyboard(' ');
    expect(screen.getByTestId('switch-state')).toHaveTextContent('Off');
  });
});

describe('GroupedRow', () => {
  it('renders a link for href and a button for onPress, named by label only', () => {
    const onPress = vi.fn();
    render(
      <>
        <GroupedRow label="Profile" value="Ada" href="/profile" />
        <GroupedRow label="Sign out" value="now" onPress={onPress} />
      </>,
    );
    const link = screen.getByRole('link', { name: 'Profile' });
    const button = screen.getByRole('button', { name: 'Sign out' });
    expect(link).toHaveAttribute('href', '/profile');
    fireEvent.click(button);
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('hides the chevron and exposes the value via aria-describedby', () => {
    render(<GroupedRow label="Language" value="English" href="/lang" />);
    const link = screen.getByRole('link', { name: 'Language' });
    expect(link.querySelector('svg')?.closest('[aria-hidden="true"]')).not.toBeNull();
    const id = link.getAttribute('aria-describedby');
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toHaveTextContent('English');
    expect(link).toHaveAccessibleDescription('English');
  });

  it('omits aria-describedby without a value and renders a plain div when not interactive', () => {
    const { container } = render(<GroupedRow label="Static" />);
    expect(container.firstElementChild?.tagName).toBe('DIV');
    expect(container.querySelector('[aria-describedby]')).toBeNull();
    render(<GroupedRow label="NoValue" onPress={() => {}} />);
    expect(screen.getByRole('button', { name: 'NoValue' })).not.toHaveAttribute('aria-describedby');
  });
});

describe('PageHeader', () => {
  const compactTitle = () => screen.getByTestId('compact-header').children[1] as HTMLElement;

  it('shows the compact title right away when not large', () => {
    render(<PageHeader title="Settings" large={false} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    expect(compactTitle()).toHaveClass('opacity-100');
  });

  it('collapses the large title to inline when the sentinel scrolls away (jsdom fallback)', () => {
    vi.stubGlobal('IntersectionObserver', undefined);
    let bottom = 10;
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const b = this.getAttribute('data-testid') === 'title-sentinel' ? bottom : 0;
      return { top: 0, bottom: b, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {} };
    });
    render(<PageHeader title="Settings" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    expect(compactTitle()).toHaveClass('opacity-0');
    bottom = -5;
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect(compactTitle()).toHaveClass('opacity-100');
    bottom = 10;
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect(compactTitle()).toHaveClass('opacity-0');
  });
});

describe('useViewportTier', () => {
  const original = window.innerWidth;
  afterEach(() => {
    window.innerWidth = original;
  });

  it.each([
    [320, 'compact'],
    [767, 'compact'],
    [768, 'wide'],
    [1279, 'wide'],
    [1280, 'desktop'],
    [2560, 'desktop'],
  ])('width %i is %s, including after a resize event', (width, tier) => {
    window.innerWidth = width;
    const { result } = renderHook(() => useViewportTier());
    expect(result.current).toBe(tier);

    window.innerWidth = 1000;
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(result.current).toBe('wide');
    window.innerWidth = width;
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(result.current).toBe(tier);
  });
});

describe('useReducedMotion / useSheetDrag', () => {
  const heights = { collapsed: 88, half: 300, full: 600 };

  function stubMedia(reduce: boolean) {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: reduce && q.includes('prefers-reduced-motion'),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  }

  it('is false when matchMedia is unavailable', () => {
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
  });

  it('disables animation under reduced motion', () => {
    stubMedia(true);
    const { result } = renderHook(() => useSheetDrag({ heights, detent: 'half', onDetentChange: () => {} }));
    expect(result.current.animate).toBe(false);
  });

  it('keeps animation otherwise, and still settles at the nearest detent', () => {
    stubMedia(false);
    const onDetentChange = vi.fn();
    const { result } = renderHook(() => useSheetDrag({ heights, detent: 'half', onDetentChange }));
    expect(result.current.animate).toBe(true);
    const el = document.createElement('div');
    const ev = (clientY: number, timeStamp: number) =>
      ({ pointerId: 1, clientY, timeStamp, button: 0, currentTarget: el }) as never;
    act(() => result.current.handlers.onPointerDown(ev(500, 0)));
    act(() => result.current.handlers.onPointerMove(ev(100, 1000)));
    expect(result.current.dragging).toBe(true);
    act(() => result.current.handlers.onPointerUp(ev(100, 1001)));
    expect(result.current.dragging).toBe(false);
    expect(onDetentChange).toHaveBeenCalledWith('full');
  });
});

describe('useDismissibleLayer', () => {
  function Layers() {
    const [outer, setOuter] = useState(false);
    const [inner, setInner] = useState(false);
    const outerBtn = useRef<HTMLButtonElement>(null);
    const innerBtn = useRef<HTMLButtonElement>(null);
    useDismissibleLayer(outer, () => setOuter(false), outerBtn);
    useDismissibleLayer(inner, () => setInner(false), innerBtn);
    return (
      <div>
        <button ref={outerBtn} onClick={() => setOuter(true)}>
          open outer
        </button>
        <button ref={innerBtn} onClick={() => setInner(true)}>
          open inner
        </button>
        {outer ? <p>outer layer</p> : null}
        {inner ? <p>inner layer</p> : null}
      </div>
    );
  }

  it('closes only the innermost layer per Escape and restores focus to its opener', async () => {
    const user = userEvent.setup();
    render(<Layers />);
    await user.click(screen.getByText('open outer'));
    await user.click(screen.getByText('open inner'));
    screen.getByText('inner layer');
    (document.activeElement as HTMLElement).blur();

    await user.keyboard('{Escape}');
    expect(screen.queryByText('inner layer')).toBeNull();
    expect(screen.getByText('outer layer')).toBeInTheDocument();
    expect(screen.getByText('open inner')).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByText('outer layer')).toBeNull();
    expect(screen.getByText('open outer')).toHaveFocus();
  });

  it('ignores Escape when no layer is open', async () => {
    const user = userEvent.setup();
    render(<Layers />);
    const ev = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true, bubbles: true });
    document.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    await user.keyboard('{Escape}');
  });
});
