import { useEffect, useState } from 'react';
import { ensureSession } from '../services/api';

/** Ensures a guest session exists (Req 22.1). */
export function useAuth(): { ready: boolean; error: string | null } {
  const [state, setState] = useState<{ ready: boolean; error: string | null }>({ ready: false, error: null });
  useEffect(() => {
    let live = true;
    ensureSession().then(
      () => live && setState({ ready: true, error: null }),
      (e: unknown) => live && setState({ ready: false, error: e instanceof Error ? e.message : String(e) }),
    );
    return () => { live = false; };
  }, []);
  return state;
}
