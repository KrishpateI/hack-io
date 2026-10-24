/**
 * Documented limitations (Req 1.5, 4.6, 8.x, 10.6). No backend endpoint serves
 * these yet, so this typed static list is the source; docs/safety-limitations.md
 * (task 14.7) is the long-form counterpart. Keep the two in sync.
 */
export interface Limitation {
  id: string;
  title: string;
  detail: string;
}

export const LIMITATIONS: readonly Limitation[] = [
  {
    id: 'prototype',
    title: 'Experimental prototype',
    detail: 'SafePath is not an official emergency service and does not replace instructions from local authorities.',
  },
  {
    id: 'cbd-historical',
    title: 'CBD plan is a 2007 historical reference',
    detail: 'Content derived from the Central Business District plan is Historical and is not a current evacuation order.',
  },
  {
    id: 'elevation',
    title: 'Terrain elevation excludes bridges, tunnels and underground levels',
    detail: 'Elevation comes from terrain data. Bridge, tunnel, elevated and lower-level segments have Low or Unknown elevation confidence.',
  },
  {
    id: 'simulated',
    title: 'Simulated scenarios and fictional building',
    detail: 'Demo disasters, closures, shelters and the indoor building are Simulated and labeled as such.',
  },
  {
    id: 'community',
    title: 'Community reports are not official',
    detail: 'Reports stay Unverified unless an official notice or an officially verified report confirms them.',
  },
  {
    id: 'unknown-data',
    title: 'Unknown data is shown as Unknown',
    detail: 'Missing attributes are never treated as favorable. Unknown conditions add routing penalties.',
  },
  {
    id: 'uic',
    title: 'UIC building plans are limited',
    detail: 'Only content cleared for public reuse is included. Excluded or unavailable UIC material is listed in its Source_Record.',
  },
  {
    id: 'transit',
    title: 'Live transit alerts may be unavailable',
    detail: 'Station entrance status stays Unknown without an official notice or an officially verified report.',
  },
  {
    id: 'no-911',
    title: 'No 911 or dispatch integration',
    detail: 'SafePath does not call, dispatch, or notify 911. Call 911 yourself for immediate emergencies.',
  },
];
