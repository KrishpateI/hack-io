import type { ReactNode } from 'react';
import { StatusIcon } from '../StatusIcon';
import { graphLabel, type DemoScenario } from './demoModel';

const btn =
  'min-h-11 rounded border border-border-subtle bg-surface-raised px-3 py-1 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-route disabled:opacity-60';

interface Props {
  scenario: DemoScenario;
  busy: boolean;
  /** Plain-language result of the last action (start, step, reset). */
  message: string | null;
  error: string | null;
  onStart: () => void;
  onNext: () => void;
  onApply: (stepId: string) => void;
  onReset: () => void;
  children?: ReactNode;
}

/** One SIMULATED scenario: step list with status and the start / next / apply / reset controls. */
export function ScenarioCard({ scenario: s, busy, message, error, onStart, onNext, onApply, onReset, children }: Props) {
  const hid = `scenario-${s.id}`;
  return (
    <article aria-labelledby={hid} className="space-y-2 rounded border border-border-subtle p-3" data-testid={`scenario-${s.id}`}>
      <header className="space-y-1">
        <h3 id={hid} className="text-lg font-semibold">
          {s.name}
          {s.optional ? <span className="ml-2 text-xs font-normal text-text-muted">(optional sample)</span> : null}
        </h3>
        <p className="text-xs text-text-muted break-all">
          SIMULATED scenario | incident {s.incidentId || 'unknown'} | {s.pristine ? 'pristine' : 'modified'} |{' '}
          {s.graphUsed ? graphLabel(s.graphUsed) : 'graph not reported'}
        </p>
      </header>

      {!s.seeded ? (
        <p className="text-sm">This scenario is not seeded yet. Press Start to seed it.</p>
      ) : (
        <ol className="space-y-1" aria-label={`Steps of ${s.name}`}>
          {s.steps.map((st) => (
            <li key={st.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="inline-flex items-center gap-1 whitespace-nowrap" data-testid={`step-status-${s.id}-${st.id}`}>
                <StatusIcon kind={st.status === 'applied' ? 'check' : 'question'} />
                {st.status === 'applied' ? 'Applied' : 'Pending'}
              </span>
              <span className="min-w-0 flex-1 break-words">
                {st.title}
                {st.id === s.nextStep ? ' (next)' : ''}
              </span>
              <button
                type="button"
                className={btn}
                onClick={() => onApply(st.id)}
                disabled={busy}
                aria-label={`Apply this step: ${st.title}`}
              >
                Apply this step
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="button" className={btn} onClick={onStart} disabled={busy}>Start</button>
        <button type="button" className={btn} onClick={onNext} disabled={busy || !s.seeded}>Next step</button>
        <button type="button" className={btn} onClick={onReset} disabled={busy}>Reset scenario</button>
      </div>

      {busy && <p role="status" className="text-sm">Working...</p>}
      {message && (
        <p role="status" className="text-sm" data-testid={`message-${s.id}`}>
          <StatusIcon kind="check" /> {message}
        </p>
      )}
      {error && (
        <p role="alert" className="rounded border border-closure p-2 text-sm" data-testid={`error-${s.id}`}>
          <StatusIcon kind="cross" /> {error}
        </p>
      )}
      {children}
    </article>
  );
}
