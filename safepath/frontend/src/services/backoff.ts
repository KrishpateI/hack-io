/** Reconnect/poll timing (Req 25.7). */
export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_CAP_MS = 30000;
export const POLL_MIN_INTERVAL_MS = 30000;
/** Stale warning must show within 10 s of disconnect (Req 25.5). */
export const STALE_WARNING_DELAY_MS = 5000;

/** Un-jittered delay for the given zero-based attempt: 1 s doubling to a 30 s cap. */
export function baseDelay(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt));
  if (n >= 5) return BACKOFF_CAP_MS; // 2^5 s = 32 s > cap
  return Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** n);
}

/**
 * Delay with jitter. Equal jitter: half fixed, half random, so the result is
 * always within [base/2, base] and therefore never above 30 s nor below 0.5 s.
 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = baseDelay(attempt);
  const r = Math.min(1, Math.max(0, random()));
  return Math.round(base / 2 + (base / 2) * r);
}

/** Polling interval is never below the 30 s minimum. */
export function pollInterval(requested: number = POLL_MIN_INTERVAL_MS): number {
  return Math.max(POLL_MIN_INTERVAL_MS, requested);
}
