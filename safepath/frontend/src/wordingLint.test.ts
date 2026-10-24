import { describe, expect, it } from 'vitest';
import { DISCLAIMER_TEXT } from './styles/tokens';

/**
 * Wording lint (Req 1.6, 1.7, 17.7, 36.3): user-facing strings must never claim
 * a route is safe/guaranteed/certified/approved, or that SafePath dispatches or
 * notifies 911. Only string literals and JSX text are scanned; comments and
 * identifiers are ignored.
 */
export const FORBIDDEN: { name: string; re: RegExp }[] = [
  { name: 'safe route', re: /\bsafe(st)?\s+(route|path|way)s?\b/i },
  { name: 'guaranteed', re: /\bguarantee[sd]?\b/i },
  { name: 'certified', re: /\bcertif(y|ied|ies|ication)\b/i },
  { name: 'approved', re: /\bapproved\b/i },
  { name: 'flood-safe', re: /\bflood[-\s]?safe\b/i },
  {
    name: '911 integration claim',
    // dispatch/notify anywhere; call/contact/alert only when SafePath or the system is the actor
    // (the imperative "call 911" addressed to the user is legitimate guidance).
    re: /\b(dispatch(es|ed|ing)?|notif(y|ies|ied|ying))\b[^.]{0,40}\b(911|emergency services?|first responders?)\b|\b(safepath|we|app|system|automatically)\s+(will\s+)?(call|contact|alert)(s|ed|ing)?\b[^.]{0,40}\b(911|emergency services?|first responders?)\b/i,
  },
];

/**
 * Documented allowlist. A string is exempt only if it is the disclaimer, or
 * it matches an explicit negation / disclaimer statement below.
 */
export const ALLOWLIST: { reason: string; test: (s: string) => boolean }[] = [
  { reason: 'Disclaimer_Banner text', test: (s) => s.includes(DISCLAIMER_TEXT) },
  {
    reason: '911 negation: "SafePath does not call, dispatch, or notify 911"',
    test: (s) =>
      /SafePath (does not|will not|never) (call|dispatch|notify|contact)[^.]*\b911\b/i.test(s),
  },
  {
    reason: 'Disclaimer: "omit any ... certified safe" style statements',
    test: (s) => /\b(omit|never (state|claim|say)|not (a )?claim)\b[^.]*\b(certified|safe|guarantee)/i.test(s),
  },
  {
    reason: 'Plain negation: "not guaranteed/certified/approved/safe"',
    test: (s) =>
      /\b(not|never|no|cannot|can't|does not|do not)\b[^.]{0,30}\b(guarantee[sd]?|certified|approved|safe route)/i.test(
        s,
      ),
  },
];

export function findViolations(text: string): string[] {
  if (ALLOWLIST.some((a) => a.test(text))) return [];
  return FORBIDDEN.filter((f) => f.re.test(text)).map((f) => f.name);
}

function stripTsComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1');
}

/** String literals plus JSX text nodes from a TS/TSX source. */
export function extractTsStrings(src: string): string[] {
  const code = stripTsComments(src);
  const out: string[] = [];
  for (const m of code.matchAll(/'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g)) {
    out.push(m[1] ?? m[2] ?? m[3] ?? '');
  }
  for (const m of code.matchAll(/>([^<>{}=;]*[A-Za-z][^<>{}=;]*)</g)) {
    out.push(m[1].replace(/\s+/g, ' ').trim());
  }
  return out;
}

/** Non-docstring string literals from a Python source (comments stripped). */
export function extractPyStrings(src: string): string[] {
  const noDoc = src.replace(/"""[\s\S]*?"""|'''[\s\S]*?'''/g, ' ');
  const out: string[] = [];
  for (const line of noDoc.split('\n')) {
    for (const m of line.matchAll(/(?<![A-Za-z0-9_])[rbfRBF]{0,2}(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")/g)) {
      out.push(m[1] ?? m[2] ?? '');
    }
  }
  return out;
}

const rawTs = import.meta.glob<string>('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true });
const rawPy = import.meta.glob<string>('../../backend/app/**/*.py', { query: '?raw', import: 'default', eager: true });

const tsFiles = Object.entries(rawTs).filter(
  ([f]) => !/\.(test|spec)\.tsx?$/.test(f) && !f.endsWith('.d.ts'),
);
// Only API explanation/wording templates are scanned on the backend.
const pyFiles = Object.entries(rawPy).filter(([f]) => /(explain|template|wording|message)/i.test(f));

describe('wording lint helpers', () => {
  it('flags forbidden claims', () => {
    for (const s of [
      'This is the safe route',
      'Guaranteed to be clear',
      'Certified path',
      'Approved by the city',
      'flood-safe corridor',
      'We notify 911 for you',
      'SafePath dispatches emergency services',
    ]) {
      expect(findViolations(s), s).not.toEqual([]);
    }
  });

  it('allows the documented disclaimer and negation statements', () => {
    for (const s of [
      DISCLAIMER_TEXT,
      'SafePath does not call, dispatch, or notify 911 or any emergency service. You must place the call yourself.',
      'Explanations omit any claim that a route is certified safe.',
      'Routes are not guaranteed to be passable.',
    ]) {
      expect(findViolations(s), s).toEqual([]);
    }
  });

  it('allows ordinary guidance text', () => {
    expect(findViolations('If you are in immediate danger, call 911 from your phone now.')).toEqual([]);
  });

  it('ignores comments in TS sources', () => {
    expect(extractTsStrings('// safe route\n/* guaranteed */ const a = "ok";')).toEqual(['ok']);
  });
});

describe('UI wording lint (Req 1.6, 1.7, 36.3)', () => {
  it('scans at least one UI source file', () => {
    expect(tsFiles.length).toBeGreaterThan(0);
  });

  it.each(tsFiles)('%s has no forbidden claims', (_n, source) => {
    const bad = extractTsStrings(source)
      .map((s) => ({ s, v: findViolations(s) }))
      .filter((x) => x.v.length);
    expect(bad).toEqual([]);
  });
});

describe('API explanation template lint (Req 17.7)', () => {
  it.each(pyFiles.length ? pyFiles : [['(no backend templates present)', '']])('%s has no forbidden claims', (_n, source) => {
    const bad = extractPyStrings(source)
      .map((s) => ({ s, v: findViolations(s) }))
      .filter((x) => x.v.length);
    expect(bad).toEqual([]);
  });
});

