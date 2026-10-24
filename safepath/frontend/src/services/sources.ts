export type DataClassification = 'Historical' | 'Static' | 'Current' | 'Simulated';
export type VerificationStatus = 'verified' | 'unverified' | 'unknown' | 'simulated';

/** Mirrors backend SourceRecord (app/models/provenance.py). */
export interface SourceRecord {
  source_id: string;
  organization: string;
  url: string;
  title: string;
  published_at?: string | null;
  retrieved_at: string;
  geographic_applicability?: string;
  data_classification: DataClassification;
  verification_status: VerificationStatus;
  page_or_section?: string | null;
  usage_restrictions?: string[];
  is_official_source: boolean;
  check_status?: 'ok' | 'unreachable' | 'not_located' | null;
}

export interface SourceFilters {
  q?: string;
  data_classification?: string;
  verification_status?: string;
}

const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export async function fetchSources(filters: SourceFilters = {}, signal?: AbortSignal): Promise<SourceRecord[]> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v && v.trim()) params.set(k, v.trim());
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/api/sources${qs ? `?${qs}` : ''}`, { signal });
  if (!res.ok) throw new Error(`Sources request failed (${res.status})`);
  return (await res.json()) as SourceRecord[];
}
