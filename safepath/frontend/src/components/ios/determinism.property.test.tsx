import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { Card } from './Card';
import { GroupedList } from './GroupedList';
import { SegmentedControl } from './SegmentedControl';
import { ToggleSwitch } from './ToggleSwitch';
import { PageHeader } from './PageHeader';
import { TabList } from './TabList';
import { TabBar } from './TabBar';
import { SidePanel } from './SidePanel';
import { InspectorPanel } from './InspectorPanel';
import { BottomSheet } from './BottomSheet';
import { stageLayout, type Detent, type PanelMode } from './layout';

const noop = () => {};
const RUNS = { numRuns: 100 };

const text = fc.string({ maxLength: 24 });
const word = fc.stringMatching(/^[a-z][a-z0-9-]{0,9}$/);
const detent = fc.constantFrom<Detent>('collapsed', 'half', 'full');
const panelMode = fc.constantFrom<PanelMode>('default', 'expanded', 'collapsed');
const iconName = fc.constantFrom('map', 'route', 'community', 'safety', 'more' as const);
const tone = fc.constantFrom('default', 'info', 'warning', 'danger', 'success' as const);

const tabItems = fc.uniqueArray(
  fc.record({ id: word, label: text, icon: iconName }),
  { selector: (t) => t.id, minLength: 1, maxLength: 6 },
);

const segmentOptions = fc.uniqueArray(fc.record({ value: word, label: text }), {
  selector: (o) => o.value,
  minLength: 1,
  maxLength: 5,
});

const groupedRows = fc.uniqueArray(
  fc.record({
    label: text,
    value: fc.option(text, { nil: undefined }),
    href: fc.option(fc.webPath(), { nil: undefined }),
    onPress: fc.option(fc.constant(noop), { nil: undefined }),
  }),
  { selector: (r) => `${r.label}-${r.href ?? ''}`, maxLength: 5 },
);

/** Server rendering gives each call a fresh, position-derived useId sequence, so equal props must give equal markup. */
function expectDeterministic(make: () => ReactElement) {
  expect(renderToStaticMarkup(make())).toBe(renderToStaticMarkup(make()));
}

// Feature: ios-style-ui-redesign, Property 13: Reusable components render deterministically
describe('Property 13: Reusable components render deterministically', () => {
  // Validates: Requirements 15.5
  it('Card', () => {
    fc.assert(
      fc.property(tone, text, text, (t, cls, body) => {
        expectDeterministic(() => <Card tone={t} className={cls}>{body}</Card>);
      }),
      RUNS,
    );
  });

  it('GroupedList', () => {
    fc.assert(
      fc.property(
        groupedRows,
        fc.option(text, { nil: undefined }),
        fc.option(text, { nil: undefined }),
        (rows, header, footer) => {
          expectDeterministic(() => (
            <GroupedList rows={rows} header={header} footer={footer} aria-label="List" />
          ));
        },
      ),
      RUNS,
    );
  });

  it('SegmentedControl', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('tabs', 'radio' as const),
        segmentOptions,
        fc.nat(10),
        text,
        (variant, options, pick, label) => {
          const value = options[pick % options.length].value;
          expectDeterministic(() => (
            <SegmentedControl variant={variant} options={options} value={value} onChange={noop} label={label} />
          ));
        },
      ),
      RUNS,
    );
  });

  it('ToggleSwitch', () => {
    fc.assert(
      fc.property(text, fc.boolean(), fc.boolean(), fc.option(text, { nil: undefined }), (label, checked, disabled, description) => {
        expectDeterministic(() => (
          <ToggleSwitch label={label} checked={checked} disabled={disabled} description={description} onChange={noop} />
        ));
      }),
      RUNS,
    );
  });

  it('PageHeader', () => {
    fc.assert(
      fc.property(text, fc.boolean(), fc.option(text, { nil: undefined }), fc.boolean(), (title, large, backLabel, hasBack) => {
        expectDeterministic(() => (
          <PageHeader title={title} large={large} backLabel={backLabel} onBack={hasBack ? noop : undefined} />
        ));
      }),
      RUNS,
    );
  });

  it('TabList', () => {
    fc.assert(
      fc.property(tabItems, fc.nat(10), fc.boolean(), (tabs, pick, rail) => {
        const current = tabs[pick % tabs.length].id;
        expectDeterministic(() => <TabList tabs={tabs} current={current} onSelect={noop} rail={rail} />);
      }),
      RUNS,
    );
  });

  it('TabBar', () => {
    fc.assert(
      fc.property(tabItems, fc.nat(10), (tabs, pick) => {
        const current = tabs[pick % tabs.length].id;
        expectDeterministic(() => <TabBar tabs={tabs} current={current} onSelect={noop} />);
      }),
      RUNS,
    );
  });

  it('SidePanel', () => {
    fc.assert(
      fc.property(
        panelMode,
        fc.integer({ min: 320, max: 2560 }),
        fc.boolean(),
        text,
        (mode, stageWidth, inspectorOpen, body) => {
          const layout = stageLayout(stageWidth, mode, inspectorOpen);
          expectDeterministic(() => (
            <SidePanel mode={mode} onModeChange={noop} layout={layout}>{body}</SidePanel>
          ));
        },
      ),
      RUNS,
    );
  });

  it('InspectorPanel', () => {
    fc.assert(
      fc.property(fc.boolean(), text, fc.integer({ min: 340, max: 400 }), text, (open, title, width, body) => {
        expectDeterministic(() => (
          <InspectorPanel open={open} title={title} width={width} onClose={noop}>{body}</InspectorPanel>
        ));
      }),
      RUNS,
    );
  });

  it('BottomSheet', () => {
    fc.assert(
      fc.property(detent, fc.integer({ min: 200, max: 1400 }), text, (d, available, body) => {
        expectDeterministic(() => (
          <BottomSheet detent={d} onDetentChange={noop} available={available}>{body}</BottomSheet>
        ));
      }),
      RUNS,
    );
  });
});
