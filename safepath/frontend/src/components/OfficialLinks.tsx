import { StatusIcon } from './StatusIcon';

export const OFFICIAL_LINKS = [
  { name: 'Chicago OEMC', url: 'https://www.chicago.gov/city/en/depts/oem.html' },
  { name: 'Ready Illinois', url: 'https://ready.illinois.gov/' },
  { name: 'UIC Ready', url: 'https://ready.uic.edu/' },
] as const;

/** Official information section of the left sidebar (Req 9.6). */
export function OfficialLinks() {
  return (
    <nav aria-label="Official information" data-testid="official-links" className="p-3">
      <h2 className="mb-2 text-sm font-semibold text-text-primary">Official information</h2>
      <ul className="space-y-1">
        {OFFICIAL_LINKS.map((l) => (
          <li key={l.url}>
            <a
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-route underline focus:outline-2 focus:outline-route"
            >
              <StatusIcon kind="building" />
              <span>{l.name}</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
