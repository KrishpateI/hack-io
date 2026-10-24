import { AppShell } from './AppShell';
import { RootLayout } from './components/RootLayout';
import { ErrorBoundary, SafeFallback } from './components/shell/ErrorBoundary';

/**
 * Top-level boundary: if the shell itself fails, the fallback still renders the disclaimer,
 * header and 911 guidance (Req 1.1, 1.3).
 */
export default function App() {
  return (
    <ErrorBoundary
      fallback={(error, reset) => (
        <RootLayout>
          <SafeFallback error={error} reset={reset} />
        </RootLayout>
      )}
    >
      <AppShell />
    </ErrorBoundary>
  );
}
