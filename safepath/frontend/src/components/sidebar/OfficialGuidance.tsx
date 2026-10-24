import guidanceData from '../../data/officialGuidance.json';

export interface GuidanceEntry {
  guidance_id: string;
  hazard: string;
  title: string;
  notes: string[];
  source_url: string;
  retrieved_at: string;
}
export const OFFICIAL_GUIDANCE = guidanceData as { disclaimer: string; guidance: GuidanceEntry[] };

/**
 * Static official-guidance panel. Data is a trimmed copy of data/sources/official_guidance.json;
 * refresh with `node scripts/sync-guidance.mjs` (from frontend/) after the backend data is re-fetched.
 */
export function OfficialGuidance({ hazard }: { hazard?: string | null }) {
  const entries = OFFICIAL_GUIDANCE.guidance.filter((g) => !hazard || g.hazard === hazard || g.hazard === 'general');
  return (
    <section aria-labelledby="guidance-h" data-testid="official-guidance" className="p-3">
      <h2 id="guidance-h" className="mb-1 text-sm font-semibold text-text-primary">Official guidance</h2>
      <p className="mb-2 text-xs font-semibold text-unverified">Static guidance, not a live order. {OFFICIAL_GUIDANCE.disclaimer}</p>
      {entries.map((g) => (
        <details key={g.guidance_id} className="mb-1 rounded border border-border-subtle p-2 text-sm">
          <summary className="cursor-pointer focus:outline-2 focus:outline-route">{g.title}</summary>
          <ul className="mt-1 list-disc pl-5">{g.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          <p className="mt-1 text-xs text-text-muted">
            Source: <a href={g.source_url} target="_blank" rel="noopener noreferrer" className="text-route underline">{g.source_url}<span className="sr-only"> (opens in a new tab)</span></a>
            {' · '}Retrieved {g.retrieved_at.slice(0, 10)}
          </p>
        </details>
      ))}
    </section>
  );
}
