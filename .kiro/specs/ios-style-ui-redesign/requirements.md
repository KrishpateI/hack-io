# Requirements Document

## Introduction

The SafePath technical build is complete. This feature restyles and rearranges the SafePath Frontend (React + TypeScript + Vite + Tailwind + MapLibre GL JS, located in `safepath/frontend`) into a clean, iOS-style interface that is easier to use under stress. The redesign covers presentation and arrangement only. Every existing feature, screen, safety label, and accessibility behavior stays reachable, and the Backend_API, API contracts, routing logic, data, and generated API client stay unchanged.

The current Frontend places a header, a left sidebar, the Map_View, a right sidebar, and a bottom action bar in one multi-column page, with a row of text links to the other screens. The redesign moves to an iOS-style interaction model in which the Map_View fills the screen and the controls float over it. Desktop and widescreen use is the primary design target: on a Wide_Viewport, content sits in a floating Side_Panel that holds a vertical Tab_List, a Desktop_Viewport adds a trailing Inspector_Panel, and the interface is keyboard-first and pointer-first with hover and focus states. Phones remain fully supported as the secondary adaptation: on a Compact_Viewport the same content sits in a Bottom_Sheet and the same tabs appear in a bottom Tab_Bar. Settings-like controls use grouped lists, Segmented_Controls, and Toggle_Switches. The Frontend supports light and dark appearance, system-font typography, large titles, rounded cards, translucent blur surfaces, subtle shadows, and motion that respects reduced-motion preferences.

This specification extends the existing `safepath-evacuation-navigation` specification. It supersedes only Requirement 36.1 of that specification (the dark-only color theme) and the layout arrangement of Requirement 34 (criteria 1, 2, 4, 5, and 6 keep their content requirements; only where the content is placed changes). All other requirements of the existing specification, including the safety, labeling, provenance, and accessibility requirements, remain in force.

## Glossary

- **Frontend**: The SafePath React web client in `safepath/frontend`.
- **Map_View**: The MapLibre GL JS map component of the Frontend.
- **Appearance**: The light or dark visual theme of the Frontend.
- **System_Appearance**: The light or dark preference reported by the browser through `prefers-color-scheme`.
- **Appearance_Setting**: The user's choice of System, Light, or Dark, stored on the device.
- **Design_Tokens**: The named color, typography, spacing, radius, shadow, blur, and motion values defined in `src/styles/tokens.ts` and `src/styles/tailwind.css`.
- **Large_Title**: A heading of at least 28 px that appears at the top of a screen and collapses to a compact inline title when the user scrolls the screen content.
- **Card**: A rounded surface that groups related content.
- **Grouped_List**: A vertical list of rows inside a rounded Card, with separators between rows and one leading label per row.
- **Segmented_Control**: A single-choice control presented as adjacent labeled segments.
- **Toggle_Switch**: A two-state control presented as a sliding switch with an accessible name and a text-visible state.
- **Side_Panel**: The primary content container on a Wide_Viewport: a floating panel anchored to the leading edge of the viewport over the Map_View, holding the Tab_List and the content of the active tab.
- **Tab_List**: The vertical list of labeled tabs at the top of the Side_Panel, each pairing an icon with a text label. It is the Wide_Viewport form of the primary navigation.
- **Inspector_Panel**: The floating panel anchored to the trailing edge of the viewport over the Map_View on a Desktop_Viewport, showing the selected map feature or selected route.
- **Tab_Bar**: The bottom navigation bar of a Compact_Viewport with the same tabs as the Tab_List, each pairing an icon with a text label.
- **Bottom_Sheet**: The Compact_Viewport adaptation of the Side_Panel: a translucent panel anchored to the bottom of the viewport over the Map_View, with Collapsed, Half, and Full detents.
- **Detent**: A resting height of the Bottom_Sheet: Collapsed, Half, or Full.
- **Keyboard_Shortcuts**: The optional two-key sequences defined in Requirement 16 that switch tabs.
- **Coarse_Pointer**: A primary input device reported by the browser through `pointer: coarse`, such as a touchscreen.
- **Fine_Pointer**: A primary input device reported by the browser through `pointer: fine`, such as a mouse or trackpad.
- **Touch_Target**: The interactive area of a control.
- **Reduced_Motion**: The browser preference reported through `prefers-reduced-motion: reduce`.
- **Safety_Chrome**: The persistent elements required by the existing specification: the Disclaimer_Banner, the SIMULATED indicator, the demo mode indicator, the connectivity status indicator, the offline and stale-data banners, the OpenStreetMap attribution, and the emergency 911 guidance.
- **Disclaimer_Banner**: The persistent text defined in the existing specification Glossary, verbatim.
- **Feature_Inventory**: The list of existing Frontend features in Requirement 2 that must remain reachable.
- **Existing_Test_Suite**: The Frontend automated tests that exist when this redesign begins, including vitest unit tests, fast-check property tests, axe accessibility tests, the wording lint test, and the design-token contrast test.
- **Wide_Viewport**: A viewport of 768 px or wider. This is the primary design target.
- **Desktop_Viewport**: A viewport of 1280 px or wider. Every Desktop_Viewport is also a Wide_Viewport.
- **Compact_Viewport**: A viewport narrower than 768 px. This is the secondary, fully supported adaptation.

## Requirements

### Requirement 1: Redesign Scope Boundary

**User Story:** As a project owner, I want the redesign to change only how the Frontend looks and is arranged, so that the finished technical work stays correct.

#### Acceptance Criteria

1. THE redesign SHALL modify only Frontend presentation code, Frontend layout code, Frontend styles, and Frontend tests that assert presentation.
2. THE Frontend SHALL send the same requests, with the same parameters, to the Backend_API as the Frontend sent before the redesign.
3. THE redesign SHALL leave the Backend_API, the OpenAPI document, the generated API client `src/services/api.generated.ts`, the Routing_Engine, and the Data_Pipeline unmodified.
4. THE redesign SHALL leave unmodified the behavior of the hooks and services in `src/hooks` and `src/services`, including route recalculation, realtime event handling, offline queueing, and backoff.
5. THE Frontend SHALL preserve every user-visible string required by the existing specification, including the Disclaimer_Banner text, "SIMULATED", "algorithm-generated", "Historical reference (2007), not a current evacuation order", "Corroborated by community — not officially verified", "Route no longer valid", "Unknown source", and "Queued — not yet received by server".
6. IF a presentation change would alter a request, a response field, or routing behavior, THEN THE redesign SHALL omit that change.

### Requirement 2: Feature Parity

**User Story:** As an existing user, I want every current feature to remain available in the new interface, so that I lose no capability.

#### Acceptance Criteria

1. THE Frontend SHALL keep each of the following Feature_Inventory items reachable and operable: active incident display, disaster type selector, incident selector, start and destination inputs with map pick mode and sample points, travel mode selection (walking, wheelchair, driving), step-free route option, route alternatives and route selection, route details screen, route uncertainty summary, route change banner, recalculate route action, map layer toggles and legend, map feature inspector for road segments, report markers, and routes, indoor building view with start selection and stairwell reporting, hazard report submission, hazard report list with corroborate and dispute actions, community messages and official notices, safety check-in, assistance request, incident overview, official guidance and official links, data sources and limitations, demo admin console, profile menu, connectivity status, offline status and report queue, and emergency 911 guidance.
2. THE Frontend SHALL keep each screen identifier in `src/components/shell/screens.ts` and each location hash route (`#/disaster-selection`, `#/route-details`, `#/submit-report`, `#/community`, `#/incident-overview`, `#/safety-check-in`, `#/official-guidance`, `#/data-sources`, `#/sources`, `#/demo`) resolving to the same content as before the redesign.
3. WHEN the demo mode is not active, THE Frontend SHALL keep the demo admin console entry point hidden, as before the redesign.
4. THE Frontend SHALL keep the existing `data-testid` attributes and accessible names that the Existing_Test_Suite queries, or SHALL update the affected tests as specified in Requirement 14.
5. THE Frontend SHALL keep each existing data-dependent state visible: loading, empty, error, stale, offline, and shelter-in-place.

### Requirement 3: Safety Chrome Preservation

**User Story:** As a member of the public, I want the safety messages to remain unmissable in the new design, so that a cleaner interface does not hide warnings.

#### Acceptance Criteria

1. THE Frontend SHALL display the Disclaimer_Banner text verbatim and untruncated on every screen, visible without user interaction at viewport widths from 320 to 2560 pixels, in both Appearances.
2. THE Frontend SHALL provide no control that dismisses, collapses, or hides the Disclaimer_Banner, and the Side_Panel, the Inspector_Panel, and the Bottom_Sheet SHALL NOT cover the Disclaimer_Banner in any state or at any Detent.
3. WHILE at least one Incident with Simulation_Flag set to true is active, THE Frontend SHALL display a "SIMULATED" indicator in the header of every screen, with a text label and an icon.
4. THE Frontend SHALL display the emergency 911 guidance in a section visually separate from messaging, check-in, and Hazard_Report features, reachable within one user interaction from every screen.
5. THE Frontend SHALL display the OpenStreetMap attribution on the Map_View, visible without user interaction, and the Side_Panel, the Inspector_Panel, and the Bottom_Sheet at every Detent SHALL NOT cover the attribution.
6. WHILE the Frontend is offline or the Realtime_Channel is disconnected, THE Frontend SHALL display the offline indicator or the stale-data warning in a position visible without scrolling in every Side_Panel state and at every Detent.
7. THE Frontend SHALL display a "SIMULATED" label on every map feature whose Simulation_Flag is true, and SHALL keep the "Official", "Community", "Simulated", and "Unknown" source labels on each item that carried them before the redesign.
8. THE Frontend SHALL convey every status with a text label or icon in addition to color.

### Requirement 4: iOS Visual Language

**User Story:** As a user, I want an interface that feels clean and familiar like an iOS app, so that I can use it without learning a new layout.

#### Acceptance Criteria

1. THE Frontend SHALL define Design_Tokens for light and dark Appearance covering background, grouped background, elevated surface, label, secondary label, separator, accent, success, warning, danger, corner radius, shadow, blur, and spacing.
2. THE Frontend SHALL render text using the system font stack (`-apple-system`, `BlinkMacSystemFont`, `system-ui`, and `sans-serif` fallbacks) in a defined type scale that includes Large_Title, title, headline, body, callout, subheadline, and caption sizes.
3. THE Frontend SHALL render Cards and Grouped_Lists with a corner radius of at least 12 px and a subtle shadow or separator that distinguishes the Card from its background.
4. THE Frontend SHALL render the Bottom_Sheet, the Side_Panel, the Tab_Bar, and the header with a translucent blurred background.
5. WHERE the browser does not support backdrop blur, THE Frontend SHALL render the translucent surfaces with an opaque fallback background that meets the contrast requirement in Requirement 12.
6. THE Frontend SHALL apply the Design_Tokens to all Frontend screens, so that no screen uses a hard-coded color outside the Design_Tokens, except map layer colors defined in `src/maps/layers`.
7. THE Frontend SHALL render a Large_Title at the top of each full-screen page.
8. WHEN the user scrolls the content of a full-screen page past its Large_Title, THE Frontend SHALL show a compact inline title in the page header.

### Requirement 5: Light and Dark Appearance

**User Story:** As a user, I want light and dark appearance, so that the interface is comfortable in daylight and at night.

#### Acceptance Criteria

1. THE Frontend SHALL support a light Appearance and a dark Appearance.
2. WHEN the Appearance_Setting is System, THE Frontend SHALL apply the System_Appearance and SHALL switch Appearance when the System_Appearance changes, without a page reload.
3. WHEN the user selects Light or Dark as the Appearance_Setting, THE Frontend SHALL apply the selected Appearance within 1 second and SHALL store the Appearance_Setting on the device.
4. WHEN the Frontend loads, THE Frontend SHALL apply the stored Appearance_Setting before the first paint of the interface, so that no flash of the opposite Appearance occurs.
5. THE Frontend SHALL provide an Appearance_Setting control with the options System, Light, and Dark as a Segmented_Control in the profile and settings area.
6. WHEN the Appearance changes, THE Map_View SHALL switch to a basemap style and layer label colors that remain legible in the new Appearance, while keeping route, closure, unverified, and verified colors distinguishable by text and icon.
7. THE Frontend SHALL replace the dark-only theme requirement (existing specification Requirement 36.1) with this Appearance model, and SHALL keep cyan or blue for navigation routes, red for confirmed closures, orange for unverified reports, and green for verified resources in both Appearances.
8. WHEN the Appearance changes, THE Map_View SHALL apply the new basemap appearance without issuing new basemap tile requests, without removing or re-adding any data layer, and without changing the basemap source or its OpenStreetMap attribution.
9. WHILE the device is offline and the PMTiles basemap file is cached, THE Map_View SHALL display the basemap in both Appearances using only the cached file.

### Requirement 6: Navigation with Tab List and Tab Bar

**User Story:** As a desktop user, I want one clear vertical list of the main areas of the app, and as a phone user I want the same areas in a bottom bar, so that I always know where I am and how to get to the next task.

#### Acceptance Criteria

1. WHERE the viewport is a Wide_Viewport, THE Frontend SHALL display the tabs Map, Route, Community, Safety, and More as a vertical Tab_List at the top of the Side_Panel.
2. WHERE the viewport is a Compact_Viewport, THE Frontend SHALL display the same tabs, with the same icons, labels, and order, as a Tab_Bar anchored to the bottom of the viewport.
3. THE Frontend SHALL provide each tab with an icon, a text label, and an accessible name, and SHALL mark the current tab with `aria-current` and a visual selected state that does not rely on color alone.
4. THE Frontend SHALL map the Feature_Inventory to tabs as follows: Map holds the map, layer toggles, legend, inspector, and incident setup; Route holds start and destination, route options, route alternatives, route details, and the recalculate action; Community holds reports, messages, official notices, check-ins, and assistance; Safety holds hazard reporting, safety check-in, assistance request, official guidance, and the emergency 911 guidance; More holds the incident overview, disaster selection, data sources and limitations, official links, the demo admin console when demo mode is active, profile, and Appearance_Setting.
5. WHEN the user selects a tab, THE Frontend SHALL show that tab's content within 300 ms and SHALL update the location hash to a route that opens the same content on reload.
6. WHEN the user selects a screen from a drill-down row, THE Frontend SHALL push the screen with a back control labeled with the title of the previous screen, and the back control SHALL return to the previous screen.
7. WHILE a pushed screen is open, THE Frontend SHALL keep the Tab_List or the Tab_Bar visible and the Safety_Chrome visible.
8. WHEN the browser back or forward control is used, THE Frontend SHALL navigate between the screens in history order.
9. THE Frontend SHALL keep the five tabs Map, Route, Community, Safety, and More as the complete tab set, because each Feature_Inventory item maps to exactly one of the five tabs in criterion 4 and no item needs a sixth tab.

### Requirement 7: Map-Centered Layout with Side Panel, Inspector Panel, and Bottom Sheet

**User Story:** As a desktop user, I want the map to fill the screen with my controls in a floating panel beside it, and as a phone user I want the same controls in a sheet over the map, so that I can see the map and the next action together on any device.

#### Acceptance Criteria

1. THE Frontend SHALL render the Map_View filling the viewport area below the header and the Disclaimer_Banner, with the Side_Panel, the Inspector_Panel, the Bottom_Sheet, and the Tab_Bar floating over the Map_View.
2. WHERE the viewport is a Wide_Viewport, THE Frontend SHALL present the Tab_List and the content of the active tab in a Side_Panel floating over the leading edge of the Map_View with a default width of 400 px that narrows to no less than 360 px when the Map_View would otherwise be narrower than 480 px, and the Map_View SHALL remain visible and interactive beside the Side_Panel.
3. WHERE the viewport is a Wide_Viewport, THE Frontend SHALL provide a Side_Panel control that expands the Side_Panel to a width of at most 720 px for dense content, limited so that the Map_View keeps the minimum width in Requirement 13.7, and a control that collapses the Side_Panel to a rail of at most 72 px that shows only the tab icons with accessible names.
4. WHEN the user changes the Side_Panel between default, expanded, and collapsed states, THE Frontend SHALL store the state on the device and SHALL restore the state on the next load of a Wide_Viewport.
5. WHERE the viewport is a Desktop_Viewport, WHEN a map feature is selected or a route is selected, THE Frontend SHALL display the feature inspector and the route summary in an Inspector_Panel floating over the trailing edge of the Map_View with a default width of 400 px that narrows to no less than 340 px when the Map_View would otherwise be narrower than 480 px, and SHALL leave the Side_Panel on the active tab.
6. WHERE the viewport is a Wide_Viewport narrower than 1280 px, WHEN a map feature is selected or a route is selected, THE Frontend SHALL display the feature inspector or route summary inside the Side_Panel and SHALL switch the Side_Panel to the Map tab for a feature or to the Route tab for a route.
7. WHEN the user activates the close control of the Inspector_Panel or presses Escape while focus is inside the Inspector_Panel, THE Frontend SHALL close the Inspector_Panel and SHALL return keyboard focus to the Map_View region.
8. WHEN the user opens the indoor building view, THE Frontend SHALL present the indoor building view inside the Side_Panel or the Bottom_Sheet and the tab model, with the "fictional and SIMULATED" labeling preserved.
9. WHILE the Map_View pick mode for start or destination is active, THE Frontend SHALL show the pick-mode hint, with the existing cancel behavior, over the Map_View in a position that is not covered by the Side_Panel, the Inspector_Panel, the Bottom_Sheet, or the Tab_Bar.
10. WHERE the viewport is a Compact_Viewport, THE Frontend SHALL present the active tab's content in a Bottom_Sheet with Collapsed, Half, and Full Detents.
11. WHEN the user drags the Bottom_Sheet grabber, THE Frontend SHALL move the Bottom_Sheet and, on release, settle it at the nearest Detent.
12. THE Frontend SHALL provide a keyboard-operable and pointer-operable alternative to dragging, consisting of a grabber control with an accessible name that cycles through the Detents, so that no Detent requires a drag gesture.
13. WHERE the viewport is a Compact_Viewport, WHEN a route is computed or a map feature is selected, THE Frontend SHALL raise the Bottom_Sheet to the Half Detent and SHALL display the route summary or the feature inspector in the Bottom_Sheet.
14. WHILE the Bottom_Sheet is at the Full Detent, THE Frontend SHALL prevent the content behind the Bottom_Sheet from receiving pointer focus, and SHALL return keyboard focus to the grabber when the Bottom_Sheet leaves the Full Detent.
15. WHILE the Map_View pick mode is active on a Compact_Viewport, THE Frontend SHALL lower the Bottom_Sheet to the Collapsed Detent.

### Requirement 8: Route Setup and Route Presentation

**User Story:** As an evacuee, I want route setup and results to be simple and readable, so that I can choose a route quickly.

#### Acceptance Criteria

1. THE Frontend SHALL present start and destination as two rows of a Grouped_List, each with a leading label, the current value or "Not set" text, and a trailing pick control.
2. THE Frontend SHALL present travel mode (Walking, Wheelchair, Driving) as a Segmented_Control with a text label for each segment, replacing the select element.
3. THE Frontend SHALL present the step-free route option as a Toggle_Switch with the label "Step-free route" and the state shown as text.
4. THE Frontend SHALL present each route alternative as a Card showing the route label, distance, estimated duration, Uncertainty_Level text label, and the "algorithm-generated" label.
5. WHEN the user selects a route Card, THE Frontend SHALL mark the Card as selected with a check icon and the `aria-pressed` or `aria-selected` state, and SHALL highlight the matching route on the Map_View.
6. THE Frontend SHALL present the route reasons, restrictions applied, excluded edge count, confidence limitations, and Source_Record references in a disclosure Grouped_List on the route details screen.
7. WHEN the Policy_Outcome is SHELTER_IN_PLACE, NO_VERIFIED_ROUTE_AVAILABLE, INSUFFICIENT_DATA, or FOLLOW_OFFICIAL_INSTRUCTIONS, THE Frontend SHALL display the outcome and its explanation in a prominent Card with an icon and a text label at the top of the Route tab.
8. WHEN a route recalculation is in progress, THE Frontend SHALL show an activity indicator with the text "Recalculating…" and SHALL disable the recalculate action, as before the redesign.
9. WHEN a route becomes invalid, THE Frontend SHALL display "Route no longer valid" with the returned Policy_Outcome or error text in a Card with an icon.

### Requirement 9: Controls, Touch Targets, and Pointer Density

**User Story:** As a user, I want controls that are compact and precise with a mouse and large and easy with a finger, so that I can operate the app quickly and accurately on any device.

#### Acceptance Criteria

1. WHERE the primary input is a Coarse_Pointer, THE Frontend SHALL render every interactive control with a Touch_Target of at least 44 by 44 CSS pixels.
2. THE Frontend SHALL render primary actions as filled buttons with a corner radius of at least 12 px and secondary actions as tinted or plain text buttons, each with a text label.
3. THE Frontend SHALL render the layer toggles as a Grouped_List of Toggle_Switches, each with the layer name as its accessible name and the on or off state exposed to assistive technology.
4. WHEN the user changes a layer Toggle_Switch, THE Map_View SHALL apply the change within 1 second.
5. THE Frontend SHALL render each Toggle_Switch with an on or off state conveyed by position and by an accessible state, in addition to color.
6. THE Frontend SHALL render destructive or irreversible actions with a confirmation step that names the action, where the action was confirmed before the redesign.
7. WHEN a control is activated, THE Frontend SHALL show a pressed state within 100 ms.
8. WHERE the primary input is a Fine_Pointer, THE Frontend SHALL render every interactive control with a Touch_Target of at least 32 by 32 CSS pixels and SHALL use the denser row height and spacing of the Fine_Pointer density.
9. WHERE the primary input is a Fine_Pointer, WHEN the pointer is over an interactive control, THE Frontend SHALL show a hover state within 100 ms that is visually distinct from the default state and from the focus indicator.
10. THE Frontend SHALL NOT make any content or action available only on hover.

### Requirement 10: Safety Actions

**User Story:** As a person in an emergency, I want the safety actions to be obvious and a single tap away, so that I can act without searching.

#### Acceptance Criteria

1. THE Frontend SHALL present Report a hazard, Request assistance, Mark safe, and View official guidance as the first rows of the Safety tab, each as a Grouped_List row with an icon, a text label, and a trailing chevron.
2. THE Frontend SHALL present Report a hazard, Request assistance, Mark safe, and View official guidance as a quick action row on the Map tab, reachable without opening another tab.
3. THE Frontend SHALL present the emergency 911 guidance as a dedicated Card on the Safety tab and as a persistent control in the header on every screen, each opening or showing the same 911 guidance text within one user interaction.
4. WHEN the user opens the hazard report form, THE Frontend SHALL present the form as a full-height sheet with labeled fields, a Segmented_Control or Grouped_List for the category, and a primary submit button with a Touch_Target of at least 44 px height.
5. WHEN the user submits "I need assistance", THE Frontend SHALL display the statement that SafePath does not dispatch emergency services together with the emergency contact guidance, as before the redesign.
6. THE Frontend SHALL omit any UI text claiming that SafePath contacts, notifies, or forwards to 911 or emergency dispatch services.

### Requirement 11: Community, Reports, and Notices

**User Story:** As a community member, I want reports, messages, and official notices organized clearly, so that I can tell official information from community information.

#### Acceptance Criteria

1. THE Frontend SHALL present the Community tab sections Reports, Messages and notices, and Check-ins and assistance as a three-segment Segmented_Control.
2. THE Frontend SHALL present Official_Notices in a Card that is visually distinct from community messages, with a text label "Official" and an icon, and SHALL present community messages with a text label "Community".
3. THE Frontend SHALL present each Hazard_Report as a Card showing the category, description, anonymous alias, creation time, Verification_Status as a text-and-icon badge, corroboration count, and the Corroborate and Dispute actions.
4. THE Frontend SHALL keep the status badge text for each Verification_Status identical to the text shown before the redesign.
5. THE Frontend SHALL keep keyboard arrow-key navigation between the segments of the Segmented_Control.
6. WHEN a realtime event updates the reports, messages, or check-ins, THE Frontend SHALL update the visible lists without a page reload and without moving the user's scroll position.

### Requirement 12: Accessibility and Contrast

**User Story:** As a user with a disability, I want the new design to remain fully accessible, so that visual polish does not reduce usability.

#### Acceptance Criteria

1. THE Frontend SHALL render all text with a contrast ratio of at least 4.5:1 against its background, and all non-text interface components and meaningful graphics with at least 3:1, in both Appearances.
2. THE Frontend SHALL verify the contrast of every text and surface Design_Token pair, in both Appearances, using the existing Design_Token contrast test, including the translucent surface opaque fallbacks.
3. THE Frontend SHALL make every interactive control reachable and operable by keyboard alone, with a visible focus indicator of at least 2 px that has at least 3:1 contrast against adjacent colors, in both Appearances.
4. THE Frontend SHALL provide an accessible name for every interactive element, including the Tab_List and Tab_Bar tabs, the Side_Panel expand and collapse controls, the Inspector_Panel close control, the Bottom_Sheet grabber, the Toggle_Switches, and the Segmented_Control segments.
5. THE Frontend SHALL expose the Tab_List and the Tab_Bar as a navigation landmark, the Side_Panel, the Inspector_Panel, and the Bottom_Sheet as labeled regions, and each Segmented_Control as a radio group or tablist with the selected segment indicated to assistive technology.
6. THE Frontend SHALL keep the skip link, and a single main landmark per page.
7. WHEN the user opens a pushed screen or a modal sheet, THE Frontend SHALL move keyboard focus to the screen title or first control, and WHEN the user closes it, THE Frontend SHALL return focus to the control that opened it.
8. WHILE the browser reports forced colors, THE Frontend SHALL keep borders, focus indicators, and selected states visible.
9. WHERE the user has set a larger browser text size up to 200 percent, THE Frontend SHALL keep all content and controls reachable without loss of content or overlap.

### Requirement 13: Motion and Responsiveness

**User Story:** As a user, I want smooth, calm motion and a layout that fits my device, so that the interface feels polished and never gets in the way.

#### Acceptance Criteria

1. THE Frontend SHALL animate screen pushes, Bottom_Sheet Detent changes, Toggle_Switch changes, and tab changes with transitions that last between 150 and 400 ms.
2. WHILE Reduced_Motion is active, THE Frontend SHALL replace slide, scale, and spring animations with an instant change or a cross-fade of at most 100 ms.
3. THE Frontend SHALL render a layout at viewport widths from 320 to 2560 pixels in which every control in the Feature_Inventory is reachable without horizontal scrolling.
4. THE Frontend SHALL position the Tab_Bar, the Bottom_Sheet, and fixed controls inside the device safe-area insets, using `env(safe-area-inset-*)` padding.
5. WHERE the viewport is a Desktop_Viewport, THE Frontend SHALL use the Wide_Viewport layout with the Side_Panel on the leading edge, the Inspector_Panel on the trailing edge, and the Map_View filling all remaining width, at every viewport width from 1280 to 2560 pixels.
6. WHERE the viewport is wider than 1600 px, THE Frontend SHALL cap the width of text content inside the Side_Panel, the Inspector_Panel, and pushed screens at 720 px so that line lengths remain readable.
7. WHERE the viewport is 1280 px or wider, THE Frontend SHALL keep the Map_View at least 480 px wide in every combination of Side_Panel state and Inspector_Panel visibility, by narrowing the Side_Panel and the Inspector_Panel to their minimum widths and by limiting the Side_Panel expansion.
8. WHEN the device orientation changes or the browser window is resized across the 768 px or 1280 px boundary, THE Frontend SHALL keep the current tab, Detent, selected route, selected map feature, and form input values.
9. THE Frontend SHALL avoid cumulative layout shift greater than 0.1 during initial load, by reserving space for the header, Disclaimer_Banner, Side_Panel, and Tab_Bar.

### Requirement 14: Existing Automated Tests Keep Passing

**User Story:** As a developer, I want the existing test suite to keep passing, so that I know the redesign did not break any behavior or safety requirement.

#### Acceptance Criteria

1. WHEN the Frontend `npm test` command runs after the redesign, THE Existing_Test_Suite SHALL pass, including the vitest unit tests, the fast-check property tests, the axe accessibility tests in `a11y.test.tsx`, the wording lint test in `wordingLint.test.ts`, and the design-token contrast test in `styles/tokens.test.ts`.
2. WHEN the Frontend `npm run build` command runs after the redesign, THE Frontend SHALL complete the TypeScript check and the production build without errors.
3. WHEN the Frontend `npm run lint` command runs after the redesign, THE Frontend SHALL complete without errors.
4. IF an existing test asserts a layout detail that the redesign intentionally changes (for example the location of a link row, a select element, or a checkbox), THEN THE redesign SHALL update that test to assert the same user-visible behavior against the new control and SHALL leave the asserted behavior unchanged.
5. THE redesign SHALL NOT delete, skip, or weaken any test assertion about safety labeling, provenance labeling, wording, disclaimer presence, stale or offline behavior, routing responses, or accessibility.
6. THE wording lint test SHALL cover all new UI text introduced by the redesign.
7. THE axe accessibility test SHALL cover the main layout at a Compact_Viewport, a Wide_Viewport, and a Desktop_Viewport, the Tab_List, the Tab_Bar, the Bottom_Sheet at each Detent, the Side_Panel in each state, the Inspector_Panel, each pushed screen, and each Appearance, and SHALL report no violations.
8. THE redesign SHALL add tests for the Appearance_Setting (System, Light, Dark, persistence, and the pre-paint application), the Tab_List and Tab_Bar mapping, the Bottom_Sheet Detent cycling by keyboard, the Segmented_Control keyboard behavior, the Reduced_Motion behavior, the viewport tier selection, the Side_Panel state persistence, the Inspector_Panel behavior, the Keyboard_Shortcuts, and the map basemap appearance switching.
9. WHERE a layout is checked by the Playwright end-to-end tests, THE redesign SHALL update only the selectors affected by the redesign and SHALL keep the asserted behavior unchanged.

### Requirement 15: Design Token and Component Structure

**User Story:** As a developer, I want the iOS look to come from a small set of reusable pieces, so that screens stay consistent and easy to change.

#### Acceptance Criteria

1. THE Frontend SHALL provide reusable presentational components for Side_Panel, Tab_List, Inspector_Panel, Tab_Bar, Bottom_Sheet, Card, Grouped_List, Segmented_Control, Toggle_Switch, and Large_Title page header, each in a documented location under `src/components/ios`.
2. THE Frontend SHALL implement each reusable component using the Design_Tokens, with no component-local color values.
3. THE Frontend SHALL implement the Design_Tokens as CSS custom properties switched by an Appearance attribute on the root element, with the TypeScript token module mirroring the values for tests.
4. THE reusable components SHALL accept content through props or children and SHALL contain no calls to the Backend_API.
5. WHEN a reusable component is rendered with a given set of props, THE reusable component SHALL produce the same markup on each render (deterministic rendering with no random or time-based output).

### Requirement 16: Desktop Keyboard and Pointer Interaction

**User Story:** As a desktop user, I want fast keyboard navigation and clear hover and focus feedback, so that I can work through the app without reaching for a touchscreen.

#### Acceptance Criteria

1. WHERE Keyboard_Shortcuts are enabled, WHEN the user presses the key "g" and then a digit from 1 to 5 within 1500 ms, THE Frontend SHALL activate the tab at that position in the order Map, Route, Community, Safety, More.
2. WHILE keyboard focus is inside a text input, a text area, a select element, or an editable region, THE Frontend SHALL ignore the Keyboard_Shortcuts.
3. WHILE the Control, Alt, or Meta key is held, THE Frontend SHALL ignore the Keyboard_Shortcuts so that no browser or assistive technology shortcut is overridden.
4. THE Frontend SHALL provide a Toggle_Switch labeled "Keyboard shortcuts" in the More tab, enabled by default, and SHALL store its state on the device.
5. WHERE Keyboard_Shortcuts are disabled, THE Frontend SHALL register no global key handler for the Keyboard_Shortcuts.
6. THE Frontend SHALL list the available Keyboard_Shortcuts as text in the More tab.
7. WHEN the user presses Escape, THE Frontend SHALL close the innermost open layer in this order: the profile menu, the 911 guidance panel, the Inspector_Panel, and the pushed screen, and SHALL return keyboard focus to the control that opened that layer.
8. WHERE the viewport is a Desktop_Viewport, THE Frontend SHALL show a visible focus indicator on every interactive control inside the Map_View overlays, the Side_Panel, and the Inspector_Panel, satisfying Requirement 12.3.
9. WHILE the pointer is over the Side_Panel or the Inspector_Panel, THE Frontend SHALL scroll the panel content with the mouse wheel and SHALL NOT zoom the Map_View.
10. WHEN the user changes the Tab_List selection with a Keyboard_Shortcut, THE Frontend SHALL move keyboard focus to the first heading of the new tab content and SHALL announce the new tab to assistive technology.
