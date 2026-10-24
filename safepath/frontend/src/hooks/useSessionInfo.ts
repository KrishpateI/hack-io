import { useEffect, useState } from 'react';
import { demoStatus, getMe, type MeInfo } from '../services/api';
import { parseStatus } from '../components/demo/demoModel';

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Profile of the current session (GET /api/auth/me). Starts once the guest session exists. */
export function useMe(ready: boolean): { me: MeInfo | null; loading: boolean; error: string | null } {
  const [state, setState] = useState<{ me: MeInfo | null; error: string | null }>({ me: null, error: null });
  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    getMe(ctl.signal).then(
      (me) => setState({ me, error: null }),
      (e: unknown) => {
        if (!ctl.signal.aborted) setState({ me: null, error: msg(e) });
      },
    );
    return () => ctl.abort();
  }, [ready]);
  return { ...state, loading: ready && !state.me && !state.error };
}

/** GET /api/demo/status. `loaded` is true once the answer (or a failure, treated as demo off) is known. */
export function useDemoStatus(): { loaded: boolean; demoMode: boolean } {
  const [state, setState] = useState({ loaded: false, demoMode: false });
  useEffect(() => {
    const ctl = new AbortController();
    demoStatus(ctl.signal).then(
      (b) => setState({ loaded: true, demoMode: parseStatus(b).demoMode }),
      () => {
        if (!ctl.signal.aborted) setState({ loaded: true, demoMode: false });
      },
    );
    return () => ctl.abort();
  }, []);
  return state;
}
