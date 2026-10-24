import type { Incident } from '../../services/api';

export type IncidentType = Incident['incident_type'];

export interface DisasterInfo {
  type: IncidentType;
  label: string;
  routing: string;
}

/** How each disaster changes routing guidance (Req 34.2). Plain language, no promises of safety. */
export const DISASTERS: readonly DisasterInfo[] = [
  { type: 'flood', label: 'Flood', routing: 'Routes avoid roads, underpasses and low-lying areas reported or modeled as flooded, and favor higher ground.' },
  { type: 'earthquake', label: 'Earthquake', routing: 'Routes avoid damaged or closed segments and areas near tall buildings; reported conditions may be incomplete.' },
  { type: 'tornado', label: 'Tornado', routing: 'Shelter guidance rather than an outdoor route: take shelter in a sturdy building and follow official instructions.' },
  { type: 'fire', label: 'Fire', routing: 'Routes avoid the reported fire area and closed segments; follow responder instructions first.' },
  { type: 'building', label: 'Building emergency', routing: 'Indoor exit guidance uses reported stairwell and exit conditions in a fictional building; follow staff and responders.' },
] as const;

export const disasterLabel = (t: string) => DISASTERS.find((d) => d.type === t)?.label ?? t;
