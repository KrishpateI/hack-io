export type SourceLabelKind = 'Official' | 'Simulated' | 'Community' | 'Unknown source';

export interface LabelableItem {
  source_record?: { is_official?: boolean | null; source_id?: string | null } | null;
  simulation_flag?: boolean | null;
  /** Set when the item originates from a community report or message. */
  origin?: 'report' | 'message' | string | null;
}

/** Req 10.4, 10.5, 36.5: exactly one label per item. */
export function classify(item: LabelableItem | null | undefined): SourceLabelKind {
  if (item?.source_record?.is_official === true) return 'Official';
  if (item?.simulation_flag === true) return 'Simulated';
  if (item?.origin === 'report' || item?.origin === 'message') return 'Community';
  return 'Unknown source';
}

/** Req 1.6: missing attribute values render as "Unknown". */
export function labelAttribute(value: unknown): string {
  if (value === null || value === undefined) return 'Unknown';
  const s = String(value).trim();
  return s === '' ? 'Unknown' : s;
}

export interface ActiveIncidentLike {
  status?: string | null;
  simulation_flag?: boolean | null;
}

/** True when any active Incident has simulation_flag true (Req 1.2). */
export function hasActiveSimulatedIncident(incidents: readonly ActiveIncidentLike[] | undefined): boolean {
  return (incidents ?? []).some((i) => i.simulation_flag === true && (i.status ?? 'active') === 'active');
}
