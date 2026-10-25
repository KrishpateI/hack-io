import { useEffect, useRef, type RefObject } from 'react';

interface Layer {
  close: () => void;
  opener: HTMLElement | null;
}

/** Open layers, innermost last. */
const stack: Layer[] = [];
let listening = false;

function onKeyDown(e: KeyboardEvent): void {
  if (e.key !== 'Escape' || e.defaultPrevented) return;
  const top = stack[stack.length - 1];
  if (!top) return;
  e.preventDefault();
  top.close();
  const opener = top.opener;
  if (opener && opener.isConnected) opener.focus();
}

function ensureListener(): void {
  if (listening || typeof document === 'undefined') return;
  document.addEventListener('keydown', onKeyDown);
  listening = true;
}

function releaseListener(): void {
  if (!listening || stack.length > 0) return;
  document.removeEventListener('keydown', onKeyDown);
  listening = false;
}

/**
 * Registers a closable layer while `open` is true. Escape closes only the
 * innermost open layer and returns focus to `openerRef` (or to the element that
 * was focused when the layer opened).
 */
export function useDismissibleLayer(
  open: boolean,
  onClose: () => void,
  openerRef?: RefObject<HTMLElement | null>,
): void {
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    const layer: Layer = {
      close: () => closeRef.current(),
      opener: openerRef?.current ?? (active instanceof HTMLElement ? active : null),
    };
    stack.push(layer);
    ensureListener();
    return () => {
      const i = stack.indexOf(layer);
      if (i >= 0) stack.splice(i, 1);
      releaseListener();
    };
  }, [open, openerRef]);
}
