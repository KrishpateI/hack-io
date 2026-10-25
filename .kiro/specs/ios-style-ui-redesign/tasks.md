# Implementation Plan: iOS-Style UI Redesign

## Overview

This plan restyles and rearranges the SafePath Frontend (`safepath/frontend`, TypeScript, React, Vite, Tailwind, MapLibre GL JS, vitest, fast-check, Playwright) into the iOS-style interface described in the design. Only presentation code, layout code, styles, and presentation tests change. Nothing under `src/hooks`, `src/services`, `src/services/api.generated.ts`, `safepath/backend`, or `docs/openapi.json` is modified (Requirement 1).

The work is ordered so the app keeps working and the Existing_Test_Suite keeps passing after each group:

1. Design tokens and Appearance (additive: the existing dark `palette` keeps its name and keys).
2. Reusable iOS components, hooks, and pure layout helpers (new files, not yet used by the app).
3. Navigation module and Escape layers (additive; `useScreenNav` stays until the wiring task).
4. Frame layer (Side_Panel, Inspector_Panel, Bottom_Sheet, Tab_List, Tab_Bar, AppFrame), built and tested standalone.
5. Tab content composition and header (new components built from existing ones, standalone).
6. Map changes and the single wiring task that switches `AppShell` to `AppFrame`. Existing tests that assert the old layout are updated immediately after, so the suite is green again at the next checkpoint.
7. Keyboard shortcuts, existing-test updates, new axe coverage, guard tests, and Playwright updates.

Decisions carried from the design: pushed screens stay non-modal `role="dialog"` (D6); five tabs (D7); the Appearance script is an external file (D3); the basemap changes Appearance by paint updates only (D4).

Conventions used by the tasks:

- Paths are relative to `safepath/frontend` unless stated otherwise.
- Each correctness property is its own sub-task using fast-check with `{ numRuns: 100 }` (properties over finite sets enumerate the whole set instead) and carries the comment `// Feature: ios-style-ui-redesign, Property {n}: {title}`.
- Property test file names are chosen so that no two property sub-tasks write the same file (this differs slightly from the design's example file names, which grouped several properties per file).
- Preferences for Side_Panel mode and Keyboard_Shortcuts are read and written through `src/components/ios/preferences.ts`; the Appearance_Setting is read and written through `src/styles/appearance.ts`.
- No task is optional. The previously pending tasks 13.9 and 13.10 of `safepath-evacuation-navigation` are not part of this plan and should be run after the redesign is complete.

## Tasks

- [x] 1. Design tokens and Appearance
  - [x] 1.1 Add the light palette and Appearance-switched CSS variables
    - In `src/styles/tokens.ts` keep `palette` (dark values, same name and keys) and `DISCLAIMER_TEXT` unchanged. Add `lightPalette`, `palettes = { dark: palette, light: lightPalette }`, the `Appearance` type, and mirrored non-color tokens (`radius`, `space`, `type` scale including Large_Title of at least 28 px, motion durations 150, 250, 350 ms)
    - In `src/styles/tailwind.css` add `@theme inline` entries mapping the existing utility names (`bg-surface`, `text-route`, `border-border-subtle`, and the rest) to CSS variables, plus new names (`--color-control-border`, `--color-glass`). Define dark values on `:root`, light values on `:root[data-appearance='light']`, and light values on `:root:not([data-appearance])` inside `@media (prefers-color-scheme: light)`
    - Define non-color tokens: `--radius-card` 14 px, `--radius-control` 12 px, shadow, blur, spacing, type scale, motion durations and easing, `--target-min` (44 px under `pointer: coarse`, 32 px under `pointer: fine`), `--glass-fallback`, and `backdrop-filter` inside `@supports` so the base rule is the opaque fallback
    - Keep the existing focus ring (3 px, 2 px offset), skip link, reduced-motion, and forced-colors rules; add hover styles under `@media (hover: hover) and (pointer: fine)` that never reveal content; set `color-scheme` per Appearance
    - Keep cyan or blue for routes, red for closures, orange for unverified, and green for verified in both palettes
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.7, 9.1, 9.8, 9.9, 9.10, 12.3, 12.8, 13.1, 13.2, 15.3_
    - _Design: Style layer, Motion, Accessibility approach, Data Models_
  - [x] 1.2 Implement Appearance resolution, persistence, and the pre-paint script
    - Create `src/styles/appearance.ts`: pure `resolveAppearance(setting, systemDark)`, `parseSetting(raw)` (any invalid value parses as `system`), `serializeSetting`; effectful `readSetting`, `writeSetting` (in-memory fallback when `localStorage` throws), `applyAppearance(root, appearance)` (sets `data-appearance`, `color-scheme`, and the single `theme-color` meta), `initAppearance()` (subscribes to `matchMedia('(prefers-color-scheme: dark)')` and `storage` events; treats a missing `matchMedia` as dark); and the `useAppearance()` hook returning `{ setting, appearance, setSetting }` via `useSyncExternalStore`
    - Create `public/appearance-init.js`, a classic script of about 15 lines with the same logic, reading `safepath.appearance`
    - Add `<script src="/appearance-init.js"></script>` to `<head>` in `index.html` before the module script; call `initAppearance()` in `src/main.tsx` before `createRoot().render`; add `/appearance-init.js` to `publicExtras` in `vite.sw-plugin.mjs` so it is precached
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.7_
    - _Design: Appearance, Data Models (persistence rules), Error Handling (storage failure, init script blocked)_
  - [x] 1.3 Write property test for Appearance resolution and pre-paint agreement
    - Create `src/styles/appearance.property.test.ts`; read `public/appearance-init.js` with `?raw` and evaluate it in jsdom with stubbed `localStorage` and `matchMedia`, the technique `sw/routing.test.ts` uses for `sw.js`
    - **Property 1: Appearance resolution and pre-paint agreement**
    - **Validates: Requirements 5.2, 5.3, 5.4**
  - [x] 1.4 Extend the Design_Token contrast test to both Appearances
    - In `src/styles/tokens.test.ts` keep all existing cases unchanged and add cases over `palettes.light` and `palettes.dark`: every text token against every surface token at 4.5:1, focus ring and control border tokens at 3:1 against every surface token, opaque `--glass-fallback` pairs, both palettes define identical keys, route/closure/unverified/verified remain distinguishable hues, and `tailwind.css` defines `--target-min` for coarse (44 px) and fine (32 px) pointers and an `@supports` guarded `backdrop-filter`
    - **Property 3: Contrast holds for every token pair in both Appearances** (exhaustive enumeration over the finite token sets)
    - **Validates: Requirements 4.5, 12.1, 12.2, 12.3**
  - [x] 1.5 Write unit tests for the Appearance_Setting
    - Create `src/styles/appearance.test.tsx`: System follows `matchMedia` changes without reload, Light and Dark apply within 1 second and persist, stored setting applies before `createRoot` render in `main.tsx` initialization, corrupt stored value falls back to System, `localStorage` throwing falls back to memory, `theme-color` meta updates
    - _Requirements: 5.2, 5.3, 5.4, 14.8_

- [x] 2. Checkpoint - Tokens and Appearance
  - Ensure all tests pass, ask the user if questions arise. Run `npm test`, `npm run build`, and `npm run lint` in `safepath/frontend`.

- [x] 3. Reusable iOS components, hooks, and layout helpers
  - [x] 3.1 Implement the pure layout helpers
    - Create `src/components/ios/layout.ts` with `viewportTier(width)`, `stageLayout(stageWidth, panelMode, inspectorOpen)` returning `StageLayout` (`panelWidth`, `inspectorWidth`, `mapWidth`, `canExpand`), `detentHeights(available)`, `nextDetent(d)`, and `nearestDetent(heights, y, velocity)`
    - `stageLayout`: Side_Panel default 400 narrowing to 360, expanded up to 720, collapsed 72, Inspector_Panel 400 narrowing to 340, Map_View at least 480 at stage widths from 1280 to 2560, `canExpand` false when expansion would shrink the map below 480
    - `detentHeights`: Collapsed 88, Half clamped between 280 and `available - 96` (midway between Collapsed and Full when `available` is below 400), Full `available - 8`
    - Export the `ViewportTier`, `PanelMode`, `Detent`, and `StageLayout` types
    - _Requirements: 6.1, 6.2, 7.2, 7.3, 7.5, 7.10, 7.11, 7.12, 13.5, 13.7_
    - _Design: Viewport tiers, Wide frame geometry, Compact frame geometry, Data Models_
  - [x] 3.2 Implement stored preferences for panel mode and Keyboard_Shortcuts
    - Create `src/components/ios/preferences.ts` with `readPanelMode`/`writePanelMode` (key `safepath.panelMode`, invalid value parses as `default`) and `readShortcuts`/`writeShortcuts` (key `safepath.shortcuts`, `on` or `off`, default `on`), each with an in-memory fallback when `localStorage` throws
    - _Requirements: 7.4, 16.4_
    - _Design: Data Models (persistence rules), Error Handling_
  - [x] 3.3 Implement the ios hooks
    - Create under `src/components/ios/hooks/`: `useViewportTier` (`matchMedia` at 768 and 1280 with `window.innerWidth` plus `resize` fallback, read through `useSyncExternalStore`), `useReducedMotion`, `useDismissibleLayer` (module-level layer stack, one document `keydown` listener closes the top layer on Escape and returns focus to its opener), and `useSheetDrag` (Pointer Events with `setPointerCapture`, settles at the nearest Detent on release and on `pointercancel`, skips animation under Reduced_Motion)
    - _Requirements: 7.11, 13.2, 13.8, 16.7_
    - _Design: Viewport tiers, Escape handling, Motion, Error Handling (pointer capture)_
  - [x] 3.4 Implement Card, GroupedList, SegmentedControl, ToggleSwitch, PageHeader, and icons
    - Create under `src/components/ios/`: `Card` (radius 14, shadow or separator, optional `tone`), `GroupedList` and `GroupedRow` (`a` when navigating, `button` otherwise, `aria-hidden` chevron, accessible name is the label only), `SegmentedControl` (`variant="tabs"` with `tablist`/`tab` and roving tabindex, `variant="radio"` with `radiogroup`/`radio`, arrow keys wrap, check glyph on the selected segment), `ToggleSwitch` (native `input type="checkbox" role="switch"`, visible label, text "On" or "Off"), `PageHeader` (Large_Title of at least 28 px, compact inline title when a sentinel leaves the scroll root via IntersectionObserver with a scroll-listener fallback), and `icons.tsx` (`TabIcon`, `Chevron`, `CheckGlyph`, inline SVG with `aria-hidden`)
    - Use only Design_Tokens (no component-local color values), size interactive parts with `min-h-[var(--target-min)]`, show a pressed state within 100 ms, make every component pure with no API calls
    - _Requirements: 4.3, 4.7, 4.8, 8.2, 8.3, 9.2, 9.5, 9.7, 11.5, 12.4, 12.5, 15.1, 15.2, 15.4_
    - _Design: Reusable iOS components, Accessibility approach_
  - [x] 3.5 Write property test for viewport tier classification
    - Create `src/components/ios/tier.property.test.ts`
    - **Property 6: Viewport tier classification is total and monotonic**
    - **Validates: Requirements 6.1, 6.2, 7.2, 7.5, 7.6, 13.5**
  - [x] 3.6 Write property test for stage layout
    - Create `src/components/ios/stageLayout.property.test.ts`
    - **Property 7: Stage layout keeps the map usable**
    - **Validates: Requirements 7.2, 7.3, 7.5, 13.5, 13.7**
  - [x] 3.7 Write property test for Detent geometry
    - Create `src/components/ios/detents.property.test.ts`
    - **Property 8: Detent geometry stays inside the stage**
    - **Validates: Requirements 3.2, 3.5, 7.1, 7.10**
  - [x] 3.8 Write property test for Detent settling and cycling
    - Create `src/components/ios/settling.property.test.ts`
    - **Property 9: Detent settling and cycling**
    - **Validates: Requirements 7.11, 7.12**
  - [x] 3.9 Write property test for stored preferences
    - Create `src/components/ios/preferences.property.test.ts` covering the Appearance_Setting, Side_Panel mode, and Keyboard_Shortcuts flag (write then read returns the same value; arbitrary stored strings read as a valid member)
    - **Property 2: Stored preferences round-trip and reject corrupt values**
    - **Validates: Requirements 5.3, 7.4, 16.4**
  - [x] 3.10 Write unit tests for the primitives and hooks
    - Create `src/components/ios/ios.test.tsx`: `SegmentedControl` arrow keys, Home/End, and selected state in both variants; `ToggleSwitch` text state and keyboard toggle; `GroupedRow` link versus button roles and chevron hidden from the accessible name; `PageHeader` large-to-inline title (with the jsdom fallback); `useViewportTier` at 320, 767, 768, 1279, 1280, and 2560 through `innerWidth` and `resize`; `useReducedMotion` branch of `useSheetDrag`; `useDismissibleLayer` closes only the innermost layer and restores focus
    - _Requirements: 11.5, 13.2, 14.8, 15.4_

- [x] 4. Navigation and Escape layers
  - [x] 4.1 Implement the navigation grammar
    - Create `src/components/shell/navigation.ts` with `TAB_IDS`, `TabId`, `TAB_TITLES`, `SCREEN_TAB` (per the design table: `disaster-selection` Map, `route-details` Route, `submit-report` Safety, `community` Community, `incident-overview` More, `safety-check-in` Safety, `official-guidance` Safety, `data-sources` More), `NavLocation`, total `parseLocation(hash)`, `formatLocation(loc)` (`#/tab/<tab>`, `#/<screen>`, `#/demo`), and `selectedTab(loc)`; keep the `#/sources` alias and leave existing `screens.ts` exports unchanged
    - _Requirements: 2.2, 6.3, 6.4, 6.5, 6.9_
    - _Design: Navigation and hash routing, Shell and navigation_
  - [x] 4.2 Implement `useNavigation`
    - Create `src/components/shell/useNavigation.ts` returning `location`, `tab`, `screen`, `demo`, `openTab`, `openScreen`, `openDemo`, `back`, `backLabel`; own the single `hashchange` subscription; push history entries storing `{ spFrom }` in `history.state`; `back()` calls `history.back()` when the previous entry was created by the app and otherwise replaces the location with the parent tab hash; `backLabel` is the previous tab or screen title
    - _Requirements: 6.5, 6.6, 6.8_
    - _Design: Navigation and hash routing, Error Handling (history.back fallback)_
  - [x] 4.3 Register Escape layers and the back control
    - Register the profile menu (`ProfileMenu.tsx`, keeps its own handler that stops propagation), the 911 panel (`Emergency911Panel.tsx`), and the pushed screen (`ScreenFrame.tsx`) with `useDismissibleLayer`; remove the private Escape handler and the Tab trap from `ScreenFrame` so it is a non-modal `role="dialog"` that keeps title focus on open and focus return on close; add a back control with the visible previous title and accessible name `Back to <title>` (new optional props, existing props unchanged)
    - _Requirements: 6.6, 6.7, 12.7, 16.7_
    - _Design: Escape handling, Decision D6, Shell and navigation_
  - [x] 4.4 Write property test for navigation location round trip and totality
    - Create `src/components/shell/navigation.property.test.ts`; include the legacy hash assertion (`#/sources` and the eight screen hashes resolve to the same screens as before)
    - **Property 4: Navigation location round trip and totality**
    - **Validates: Requirements 2.2, 6.5**
  - [x] 4.5 Write unit tests for `useNavigation` and the back control
    - Create `src/components/shell/useNavigation.test.tsx`: tab push updates the hash, history Back and Forward walk in order, `back()` fallback after a deep link, `backLabel` text; add a `ScreenFrame` case to `src/components/shell/shell.test.tsx` for the `Back to <title>` name; confirm Escape closes the profile menu, then the 911 panel, then the pushed screen, in that order
    - _Requirements: 6.5, 6.6, 6.8, 16.7_

- [x] 5. Checkpoint - Components and navigation
  - Ensure all tests pass, ask the user if questions arise. Run `npm test`, `npm run build`, and `npm run lint` in `safepath/frontend`.

- [x] 6. Frame layer
  - [x] 6.1 Implement TabList and SidePanel
    - Create `src/components/ios/TabList.tsx` (`nav aria-label="Primary"`, vertical, icon plus text label, `aria-current="page"` plus a non-color selected state, rail mode hides labels visually but keeps accessible names) and `SidePanel.tsx` (`region` named "SafePath panel", expand and collapse controls with accessible names, disabled Expand with description when `canExpand` is false, width from `stageLayout`, mode stored and restored through `preferences.ts`, internal scroll area, wheel scrolls the panel and does not zoom the map, text capped at 720 px above 1600 px)
    - _Requirements: 6.1, 6.3, 7.2, 7.3, 7.4, 12.4, 12.5, 13.6, 16.9_
    - _Design: Wide frame geometry, Reusable iOS components_
  - [x] 6.2 Implement InspectorPanel
    - Create `src/components/ios/InspectorPanel.tsx`: `region` named "Map feature details", close control with accessible name, Escape registered as a dismissible layer, focus returns to the Map_View region (`data-focus-target`); position leaves the MapLibre navigation control and the attribution uncovered
    - _Requirements: 3.5, 7.5, 7.7, 12.4, 12.5, 16.7, 16.8_
    - _Design: Wide frame geometry, Escape handling_
  - [x] 6.3 Implement TabBar and BottomSheet
    - Create `src/components/ios/TabBar.tsx` (same tabs, icons, labels, and order as TabList, `nav aria-label="Primary"`, `env(safe-area-inset-bottom)` padding) and `BottomSheet.tsx` (`region` named "SafePath panel", grabber `button` with accessible name that cycles Detents with Enter, Space, and Arrow Up/Down, pointer drag through `useSheetDrag`, heights from `detentHeights`, `inert` plus `aria-hidden` fallback on the map at Full, focus returns to the grabber when leaving Full, 150 to 400 ms height transition, safe-area padding)
    - _Requirements: 6.2, 6.3, 7.10, 7.11, 7.12, 7.14, 12.4, 12.5, 13.1, 13.4_
    - _Design: Compact frame geometry, Reusable iOS components_
  - [x] 6.4 Implement PanelBody and AppFrame
    - Create `src/components/ios/PanelBody.tsx` (persistent detached `div` rendered through `createPortal`, appended into the active frame's body slot in a layout effect, focus restored by element `id` after the move, fallback to the panel title)
    - Create `src/components/shell/AppFrame.tsx`: reads `useViewportTier()` and `useNavigation()`, renders the stage (`data-testid="dashboard"`, `data-viewport-tier`, `min-height` 28 rem), `MapView`, the right frame (Side_Panel plus Inspector_Panel at desktop; Side_Panel only at wide with the inspector shown inside the Map tab; Bottom_Sheet plus Tab_Bar at compact with the attribution strip between sheet and Tab_Bar and `--map-attribution-bottom` set), and `PickHint` (`data-testid="pick-hint"`, same text and cancel behavior, clear of every panel); takes the five tab contents and the pushed screen as render props; raises the sheet to Half when a route is computed or a feature is selected and lowers it to Collapsed during pick mode; the Route tab shows a text-and-icon "Updated" badge when a `RouteChangeBanner` exists off-tab; keeps tab, Detent, selection, and form values across tier changes; Disclaimer_Banner, header, and offline banner stay outside the stage
    - _Requirements: 3.2, 3.5, 3.6, 6.7, 7.1, 7.5, 7.6, 7.8, 7.9, 7.13, 7.15, 13.3, 13.4, 13.5, 13.8, 13.9_
    - _Design: Page structure, Panel body portal, Compact frame geometry, Tab contents_
  - [x] 6.5 Write property test for the five tabs in TabList and TabBar
    - Create `src/components/ios/tabs.property.test.tsx` (exhaustive over the five current tabs for both components)
    - **Property 12: Navigation components expose the same five tabs**
    - **Validates: Requirements 6.1, 6.2, 6.3, 12.4**
  - [x] 6.6 Write property test for deterministic rendering
    - Create `src/components/ios/determinism.property.test.tsx` covering Card, GroupedList, SegmentedControl, ToggleSwitch, PageHeader, TabList, TabBar, SidePanel, InspectorPanel, and BottomSheet with generated props
    - **Property 13: Reusable components render deterministically**
    - **Validates: Requirements 15.5**
  - [x] 6.7 Write unit tests for the frame components
    - Create `src/components/ios/frame.test.tsx`: Bottom_Sheet grabber cycles Collapsed, Half, Full with Enter, Space, and arrow keys and leaves no Detent drag-only; focus returns to the grabber leaving Full; Side_Panel expand, collapse, and stored mode restore; disabled Expand when `canExpand` is false; Inspector_Panel close control, Escape, and focus return; PanelBody keeps typed input and focus when the host moves
    - _Requirements: 7.3, 7.4, 7.7, 7.11, 7.12, 7.14, 13.8, 14.8_
  - [x] 6.8 Write component tests for AppFrame
    - Create `src/components/shell/AppFrame.test.tsx` using the `setViewport` helper at 320, 1024, 1280, and 2560: Tab_Bar plus Bottom_Sheet on compact; Tab_List plus Side_Panel on wide; Inspector_Panel on desktop and in-panel inspector below it; state survives a tier change (typed coordinate and selected tab); pick mode lowers the sheet and shows `pick-hint`; Disclaimer_Banner, header, and attribution are not inside any panel; Route tab "Updated" badge when the change banner exists off-tab
    - _Requirements: 3.2, 3.5, 6.1, 6.2, 7.1, 7.5, 7.6, 7.9, 7.15, 13.8, 14.8_

- [x] 7. Tab content composition and header
  - [x] 7.1 Restyle the Header and move the 911 control into it
    - Extract a shared `Emergency911Guidance` text component used by both the header popover and a Safety tab Card (same strings, no claim that SafePath contacts or notifies 911); render the control in `Header.tsx` outside `main` with the accessible name `Emergency: call 911` and keep `emergency-911` and `emergency-911-body` ids; popover is a `section`, not `aside`; remove the fixed bottom-right panel from `RootLayout.tsx`; keep `header-city`, `demo-mode-indicator`, `simulated-indicator-slot` (SIMULATED with icon and text), connectivity, and profile; translucent token-based surface with opaque fallback
    - _Requirements: 3.3, 3.4, 3.8, 4.4, 4.5, 10.3, 10.6_
    - _Design: Page structure, Components and Interfaces (Emergency911Panel, Header)_
  - [x] 7.2 Compose MapTab and restyle the quick actions
    - Create `src/components/shell/tabs/MapTab.tsx` (props only, no API calls): `ActiveIncident` Card, quick action row (`BottomControls`, restyled, `onRecalculate` optional so the Map tab renders four actions and the existing test with five still passes), inline `DisasterSelector` (keeps `disaster-selector`), "Select a disaster" row, a `layerControls` slot, the feature inspector when the tier is below desktop, and the indoor building view when the incident is a building (Side_Panel opens expanded)
    - _Requirements: 2.1, 6.4, 7.6, 7.8, 10.2_
    - _Design: Tab contents (Map), Feature Parity Map_
  - [x] 7.3 Compose RouteTab and restyle route setup and results
    - Create `src/components/shell/tabs/RouteTab.tsx`: outcome Card for SHELTER_IN_PLACE, NO_VERIFIED_ROUTE_AVAILABLE, INSUFFICIENT_DATA, and FOLLOW_OFFICIAL_INSTRUCTIONS plus `shelter-note` at the top; `StartDestinationPicker` as a Grouped_List with "Not set" text and trailing pick control (keeps `#start-lat` and related ids); travel mode `SegmentedControl variant="radio"` (Walking, Wheelchair, Driving); step-free `ToggleSwitch` labeled "Step-free route" (hidden for indoor, as today); `RouteUncertaintySummary`; `RouteChangeBanner`; route alternatives as selectable Cards in `RoutePanel` (label, distance, duration, Uncertainty_Level text, "algorithm-generated", check icon and `aria-pressed` when selected, "Recalculating…" and disabled recalculate, "Route no longer valid" Card with icon); "Route details" row; disclosure Grouped_List on `RouteDetailsScreen`
    - _Requirements: 2.1, 2.5, 6.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 9.2_
    - _Design: Tab contents (Route), Feature Parity Map_
  - [x] 7.4 Compose CommunityTab
    - Create `src/components/shell/tabs/CommunityTab.tsx` using `RightSidebarView` re-skinned with `SegmentedControl variant="tabs"` (Reports, Messages and notices, Check-ins and assistance); props, test ids, `tablist`/`tab`/`aria-selected`, and arrow-key behavior unchanged; Hazard_Report Cards with the same badge text and Corroborate and Dispute actions; Official_Notices in a distinct Card labeled "Official" with icon, community messages labeled "Community"; "Community communications" row; realtime updates keep scroll position
    - _Requirements: 2.1, 3.7, 6.4, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_
    - _Design: Tab contents (Community), Feature Parity Map_
  - [x] 7.5 Compose SafetyTab
    - Create `src/components/shell/tabs/SafetyTab.tsx`: first rows Report a hazard, Request assistance, Mark safe, View official guidance (icon, text label, trailing chevron) opening the existing pushed screens; dedicated 911 Card using `Emergency911Guidance`, visually separate from the other rows; `OfficialGuidance` for the selected hazard
    - _Requirements: 2.1, 3.4, 6.4, 10.1, 10.3, 10.6_
    - _Design: Tab contents (Safety), Feature Parity Map_
  - [x] 7.6 Compose MoreTab
    - Create `src/components/shell/tabs/MoreTab.tsx`: rows Incident overview, Select a disaster, Data sources and limitations, and Demo console (rendered only when demo mode is active); `OfficialLinks`; account details through `ProfileDetails` extracted from `ProfileMenu.tsx`; Settings group with the Appearance `SegmentedControl variant="radio"` (System, Light, Dark), the "Keyboard shortcuts" `ToggleSwitch` (props `shortcutsEnabled` and `onShortcutsChange`), and the shortcut list as text (`g` then `1` to `5`)
    - _Requirements: 2.3, 5.5, 6.4, 16.4, 16.6_
    - _Design: Tab contents (More), Feature Parity Map_
  - [x] 7.7 Restyle ScreenFrame and the pushed screens
    - Restyle `ScreenFrame.tsx` with the `PageHeader` Large_Title, compact inline title on scroll, back control, and a 720 px text cap above 1600 px; keep the non-modal `role="dialog"` and the screen titles; present `ReportForm` (`#/submit-report`) as a full-height sheet with labeled fields, a `SegmentedControl` or Grouped_List for the category, and a primary submit button with a Touch_Target of at least 44 px height; the assistance submission keeps the statement that SafePath does not dispatch emergency services with the emergency contact guidance; confirm steps remain where they existed before
    - _Requirements: 4.7, 4.8, 6.6, 6.7, 9.6, 10.4, 10.5, 12.7, 13.6_
    - _Design: Shell and navigation (ScreenFrame), Decision D6, Feature Parity Map_
  - [x] 7.8 Write tab mapping tests and the Feature_Inventory property test
    - Create `src/components/shell/tabs.test.tsx`: each tab's content contains its Feature Parity Map entries; demo console entry hidden when demo mode is off; every screen row opens the matching screen hash
    - **Property 5: Every screen and Feature_Inventory item has exactly one home tab** (exhaustive over `SCREEN_IDS` and the Feature_Inventory table)
    - **Validates: Requirements 2.1, 2.2, 6.4, 6.9**

- [x] 8. Checkpoint - Frame and tab content
  - Ensure all tests pass, ask the user if questions arise. Run `npm test`, `npm run build`, and `npm run lint` in `safepath/frontend`. The app still renders the old layout at this point because `AppShell` is not rewired yet.

- [x] 9. Map changes and AppShell wiring
  - [x] 9.1 Add basemap Appearance support
    - Create `src/maps/basemapAppearance.ts` with `basemapPaint(appearance, kind)` (background `#0b1220` dark and `#f2f2f7` light; vector water and roads; raster `basemap` brightness, saturation, and contrast values from the design) and `applyBasemapAppearance(map, appearance, kind)` (skips missing layers, re-applies on the map `load` event)
    - Add an optional `appearance` parameter (default `dark`, reproducing today's output) to `buildBasemapStyle` in `src/maps/pmtiles.ts` without changing sources, URL, or attribution
    - _Requirements: 5.6, 5.8, 5.9_
    - _Design: Map style and Appearance, Error Handling (setPaintProperty before load)_
  - [x] 9.2 Write property test for basemap Appearance invariants
    - Create `src/maps/basemapAppearance.property.test.ts` plus example cases for `basemapPaint` values and `applyBasemapAppearance` with a fake map
    - **Property 11: Appearance never changes the basemap source or layer set**
    - **Validates: Requirements 5.6, 5.8, 5.9, 3.5**
  - [x] 9.3 Add `MapLayers.setAppearance`
    - Add `setAppearance(appearance)` to `MapLayers` for the default-color label layers (`elevation-label`, `hazards-label`, `fire-label`, `flood-label`, `cta-stations-label`): dark text with a light halo in Light, light text with a dark halo in Dark; status-colored labels and the route casing keep their colors; route, closure, unverified, and verified colors do not change; add an optional `setPaintProperty` to `MapLike` so existing fake maps keep working; no data layer is removed or re-added
    - _Requirements: 5.6, 5.7, 5.8_
    - _Design: Map style and Appearance_
  - [x] 9.4 Make MapView controlled and add MapLayerControls
    - Extract `useLayerToggles()` from `MapView` state; add `MapLayerControls` (Grouped_List of Toggle_Switches, each named by layer name with exposed on/off state, plus the existing `Legend`) replacing `LayerToggles.tsx`; `MapView` takes `layerVisibility`, `appearance`, and `viewPadding` props, applies visibility to `MapLayers`, the extraction layer, and `useArtifactLayers` within 1 second, calls `map.setPadding` (animated with `easeTo` only when motion is allowed), no longer renders the `details` panel, and positions the attribution with `--map-attribution-bottom`; the attribution element and extraction label chip use token classes instead of hard-coded hex values; keep the `map-error` alert for WebGL failure
    - _Requirements: 3.5, 4.6, 5.6, 5.8, 9.3, 9.4, 9.5_
    - _Design: Map (src/maps), Wide frame geometry, Error Handling (WebGL unavailable)_
  - [x] 9.5 Wire AppFrame into AppShell
    - In `AppShell.tsx` keep every hook, state variable, and request unchanged; replace the grid, the "Screens" nav, and the inline select and checkbox with `AppFrame` and the five tab contents; hold `useLayerToggles()` in `AppShell` and pass `layerVisibility` to `MapView` and `controls` to `MapTab`; pass `useAppearance()` to `MapView`; replace `useScreenNav` and the duplicate hash subscription with `useNavigation`; wrap every tab and pushed screen in `ErrorBoundary`; render `FeatureInspector` in the Inspector_Panel (desktop), the Map tab (wide), or the Bottom_Sheet (compact) from the single `useFeatureInspector` source; retire `LeftSidebar.tsx` and `useScreenNav.ts` once nothing imports them
    - _Requirements: 1.2, 1.4, 2.1, 2.2, 2.3, 2.5, 6.4, 6.7, 7.1, 7.5, 7.6, 7.13_
    - _Design: Decision D1, D5, Architecture (Layers), Feature Parity Map_
  - [x] 9.6 Update the map tests for the controlled MapView
    - In `src/maps/MapView.test.tsx` add `setPaintProperty` and `setPadding` to the fake `Map`; drive the layer-toggle test through the `layerVisibility` prop and assert `setLayoutProperty` and the extraction label; add a `MapLayerControls` test for the switch name `Data extraction boundary`; keep all `buildBasemapStyle` cases and add Appearance cases; in `src/maps/layers/layers.test.tsx` keep existing cases and add `setAppearance` cases with the fake map
    - _Requirements: 5.6, 9.3, 9.4, 14.4, 14.5_

- [x] 10. Keyboard shortcuts
  - [x] 10.1 Implement the shortcut reducer and hook
    - Create `src/components/shell/useKeyboardShortcuts.ts` with pure `shortcutReducer(state, event, enabled)` returning `{ state, tab }` (`g` arms for 1500 ms, digits 1 to 5 select Map, Route, Community, Safety, More, ignored while Control, Alt, or Meta is held or focus is in an input, textarea, select, or editable region) and the hook that registers no global `keydown` handler when shortcuts are disabled, moves focus to the first heading of the new tab content, and updates an `aria-live="polite"` announcer
    - _Requirements: 16.1, 16.2, 16.3, 16.5, 16.10_
    - _Design: Components and Interfaces (useKeyboardShortcuts), Data Models (Shortcuts)_
  - [x] 10.2 Write property test for shortcut sequences
    - Create `src/components/shell/shortcuts.property.test.ts` comparing `shortcutReducer` with a small oracle implemented in the test
    - **Property 10: Keyboard shortcut sequences match the oracle**
    - **Validates: Requirements 16.1, 16.2, 16.3, 16.5**
  - [x] 10.3 Wire shortcuts and the Shortcuts setting
    - In `AppShell.tsx` read the enabled flag through `preferences.ts` and pass it with its setter to `MoreTab`; mount `useKeyboardShortcuts` with `useNavigation().openTab`; mark tab headings and the Map_View region with `data-focus-target`
    - _Requirements: 16.1, 16.4, 16.5, 16.6, 16.10_
  - [x] 10.4 Write unit tests for shortcut wiring
    - Create `src/components/shell/useKeyboardShortcuts.test.tsx`: listener registered only when enabled, `g` then `3` opens Community and focuses its heading, announcer text, ignored inside text inputs, the Shortcuts toggle persists across remount
    - _Requirements: 16.4, 16.5, 16.10, 14.8_

- [x] 11. Update existing tests for the new layout
  - [x] 11.1 Update `App.test.tsx`
    - Add an `openTab('Route')` helper that clicks the Tab_List link named "Route"; open the owning tab before using "Use sample points", `route-item`, the screen links, and `link "Demo console"` (More tab); replace `expect(dashboard).not.toBeVisible()` with: the dialog is present, the Tab_List and Disclaimer_Banner stay visible, and the dialog is gone after Escape; replace the `complementary` landmark count with named `region` and `navigation` landmarks; use `queryAllByRole` including `switch` for the control checks; keep finding the 911 button by name inside `banner`; do not remove or weaken any safety, wording, stale, offline, or routing assertion
    - _Requirements: 2.4, 14.1, 14.4, 14.5_
  - [x] 11.2 Update `a11y.test.tsx`
    - Keep `PAGES` hashes, ready probes, and the `demo-route` and `dialog` names; add widths 768 and 1280 beside 320 and 2560; run the axe test at 320, 1024, and 1440 in both Appearances by setting `data-appearance` on `document.documentElement`; reach the More tab for the keyboard test with `g` then `5`; replace "911 is the last tab stop" with: reachable within the first ten Tab stops after the skip link, outside `main`, toggles with Enter and Space; exempt programmatic focus targets marked `data-focus-target` from the `tabIndex >= 0` check while still requiring an accessible name; report no axe violations
    - _Requirements: 12.6, 14.1, 14.4, 14.5, 14.7_
  - [x] 11.3 Update `shell.test.tsx` and `sidebar.test.tsx`
    - `shell.test.tsx`: keep `RightSidebarView` tablist behavior, `BottomControls` five buttons with `onRecalculate`, and `ScreenFrame` dialog, title focus, Escape, and focus return
    - `sidebar.test.tsx`: render `ActiveIncident`, `DisasterSelector`, and `StartDestinationPicker` in the `Harness` with the same props and keep the incident-list, SIMULATED label, sample-point, and coordinate-error assertions; render `OfficialLinks` and `OfficialGuidance` directly for the links and static guidance assertions; replace the "collapses on small screens" test with Bottom_Sheet Detent assertions (it asserted a layout detail that no longer exists); leave the `RouteUncertaintySummary` test unchanged
    - _Requirements: 14.1, 14.4, 14.5_
  - [x] 11.4 Run the regression guard over the remaining existing tests
    - Run `community`, `route`, `offline`, `inspect`, `indoor`, `DemoAdmin`, `SourcesLimitations`, `classification`, `labels`, `staleBanner.integration`, `ErrorBoundary`, `RootLayout`, `wordingLint`, and `layers` tests; investigate and fix the component (not the assertion) for any failure; confirm `wordingLint.test.ts` scans the new `src/components/ios` and tab files without edits
    - _Requirements: 14.1, 14.5, 14.6_

- [x] 12. New accessibility and guard tests
  - [x] 12.1 Write axe coverage for frames, Detents, pushed screens, and Appearances
    - Create `src/a11y.frames.test.tsx`: axe with no violations for the main layout at compact, wide, and desktop tiers; Tab_List; Tab_Bar; Bottom_Sheet at Collapsed, Half, and Full; Side_Panel in default, expanded, and collapsed states; Inspector_Panel; every pushed screen; and each of those in both Appearances; assert one `main` landmark and the skip link; assert no `Disclaimer_Banner` dismiss control exists at any Detent
    - _Requirements: 3.1, 3.2, 12.4, 12.5, 12.6, 14.7_
  - [x] 12.2 Write the hard-coded color guard test
    - Create `src/styles/noHardcodedColors.test.ts`: read source text of `src/components/**`, `src/pages/**`, and `src/maps/*.tsx` (excluding `src/maps/layers` and test files) and assert no `#rrggbb` literals; extend to confirm reusable `src/components/ios` files contain no color literals at all
    - _Requirements: 4.6, 15.2_

- [x] 13. Checkpoint - Unit, property, and accessibility suites
  - Ensure all tests pass, ask the user if questions arise. Run `npm test`, `npm run build`, and `npm run lint` in `safepath/frontend`, then run `git diff --stat` and confirm there are no changes under `src/hooks`, `src/services`, `src/services/api.generated.ts`, `safepath/backend`, or `docs/openapi.json`.

- [x] 14. Playwright end-to-end updates
  - [x] 14.1 Update `e2e/helpers.ts` and `scenario1-flood.spec.ts`
    - Keep `openApp` unchanged; add `openTab(page, name)` that clicks the Tab_List link of that name; `openConsoleAsModerator` calls `openTab(page, 'More')` before clicking `Demo console`; in scenario 1 replace `left-sidebar` with `disaster-selector`, call `openTab(v, 'Route')` before filling `#start-lat` and the destination fields, keep `route-panel` and `route-change-banner`, and replace the final "Back to the map" link with the back control `Back to More`; asserted behavior is unchanged
    - _Requirements: 14.9_
  - [x] 14.2 Update `scenario3-stairwell.spec.ts`
    - Switch tabs for the flow: Map tab for the indoor view checks, Community tab for the report card and corroboration, back to the Map tab before the moderator confirmation so the 5-second closure timing is measured against a mounted indoor view; all timings and assertions keep their meaning
    - _Requirements: 14.9_
  - [x] 14.3 Update `viewport.spec.ts`
    - Add widths 768 and 1280 to `WIDTHS`; add 2560 desktop checks for Side_Panel plus Inspector_Panel geometry (Map_View width of at least 480 from bounding boxes); verify the `Report hazard` locator against the current "Report a hazard" label from the run output before changing it; route the `Demo console` step through the More tab
    - _Requirements: 3.1, 13.3, 13.5, 13.7, 14.9_
  - [x] 14.4 Add `e2e/appearance-and-density.spec.ts`
    - Emulate `prefers-color-scheme` dark and light and assert the root attribute is set before first paint; check focus ring width and contrast in both Appearances; measure Touch_Target sizes under coarse (44 px) and fine (32 px) pointer emulation; run the offline basemap check (cache the PMTiles file, go offline, switch Appearance, assert no tile network request and visible attribution), skipping that step with a clear reason when no PMTiles fixture exists
    - _Requirements: 5.4, 5.8, 5.9, 9.1, 9.8, 12.3, 14.9_

- [x] 15. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise. Run `npm test`, `npm run build`, `npm run lint`, and `npm run e2e` in `safepath/frontend`, and run `git diff --stat` to confirm the Backend_API, generated client, `src/hooks`, and `src/services` are unchanged.

## Notes

- All tasks are required; none are optional.
- Each task references specific requirements and design sections for traceability.
- Checkpoints 2, 5, 8, 13, and 15 ensure incremental validation.
- Property tests validate universal correctness properties 1 to 13 from the design, one property per sub-task.
- After this redesign is complete, run the previously pending tasks 13.9 and 13.10 of `safepath-evacuation-navigation`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "3.1", "3.2", "4.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "4.2", "4.4", "10.1"] },
    { "id": 2, "tasks": ["1.3", "1.5", "3.9", "3.10", "4.3", "4.5", "10.2"] },
    { "id": 3, "tasks": ["6.1", "6.2", "6.3", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "9.1", "9.3"] },
    { "id": 4, "tasks": ["6.4", "6.5", "6.6", "6.7", "7.8", "9.2", "9.4"] },
    { "id": 5, "tasks": ["6.8", "9.5", "9.6"] },
    { "id": 6, "tasks": ["10.3", "11.1", "11.3"] },
    { "id": 7, "tasks": ["10.4", "11.2", "11.4", "12.1", "12.2"] },
    { "id": 8, "tasks": ["14.1"] },
    { "id": 9, "tasks": ["14.2", "14.3", "14.4"] }
  ]
}
```
