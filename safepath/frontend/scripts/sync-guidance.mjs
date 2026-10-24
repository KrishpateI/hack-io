// Refresh the trimmed official guidance copy used by the sidebar.
// Run from the frontend folder:  node scripts/sync-guidance.mjs
// Source: ../data/sources/official_guidance.json -> src/data/officialGuidance.json
import { readFileSync, writeFileSync } from 'node:fs';

const src = JSON.parse(readFileSync(new URL('../../data/sources/official_guidance.json', import.meta.url), 'utf8'));
const out = {
  disclaimer: src.disclaimer,
  guidance: src.guidance.map((g) => ({
    guidance_id: g.guidance_id,
    hazard: g.hazard,
    title: g.title,
    notes: g.notes,
    source_url: g.source_url,
    retrieved_at: g.retrieved_at,
  })),
};
writeFileSync(new URL('../src/data/officialGuidance.json', import.meta.url), JSON.stringify(out, null, 2) + '\n');
console.log(`wrote ${out.guidance.length} guidance entries`);
