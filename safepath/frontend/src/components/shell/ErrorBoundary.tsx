import { Component, type ErrorInfo, type ReactNode } from 'react';
import { CALL_911_TEXT } from '../community/NoDispatchNotice';

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Custom fallback; the default shows a safe message with 911 guidance. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface State {
  error: Error | null;
}

export function SafeFallback({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <section role="alert" data-testid="error-fallback" className="m-3 space-y-3 rounded-lg border-2 border-closure bg-surface p-4 text-sm text-text-primary">
      <h2 className="text-lg font-bold">Something went wrong</h2>
      <p>SafePath could not display this part of the app. Your data was not changed.</p>
      <p className="font-semibold">{CALL_911_TEXT}</p>
      <p>
        Follow instructions from local authorities. Official information:{' '}
        <a className="text-route underline" href="https://www.chicago.gov/city/en/depts/oem.html" target="_blank" rel="noopener noreferrer">
          Chicago OEMC<span className="sr-only"> (opens in a new tab)</span>
        </a>
        .
      </p>
      <p className="break-words text-xs text-text-muted">Technical detail: {error.message || 'unknown error'}</p>
      <button
        type="button"
        onClick={reset}
        className="min-h-11 rounded border border-route px-3 text-sm font-semibold text-route"
      >
        Try again
      </button>
    </section>
  );
}

/** Catches render errors so the disclaimer and 911 guidance stay available (Req 1.1, 1.3). */
export class ErrorBoundary extends Component<ErrorBoundaryProps, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  reset = (): void => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return this.props.fallback ? this.props.fallback(error, this.reset) : <SafeFallback error={error} reset={this.reset} />;
  }
}
