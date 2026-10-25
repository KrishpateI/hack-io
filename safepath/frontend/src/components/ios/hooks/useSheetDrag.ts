import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { nearestDetent, type Detent, type DetentHeights } from '../layout';
import { useReducedMotion } from './useReducedMotion';

export interface UseSheetDragOptions {
  heights: DetentHeights;
  detent: Detent;
  onDetentChange: (next: Detent) => void;
}

export interface SheetDragHandlers {
  onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void;
  onPointerMove: (e: ReactPointerEvent<HTMLElement>) => void;
  onPointerUp: (e: ReactPointerEvent<HTMLElement>) => void;
  onPointerCancel: (e: ReactPointerEvent<HTMLElement>) => void;
}

export interface SheetDrag {
  /** Live height in px while dragging, otherwise null (use the Detent height). */
  dragHeight: number | null;
  dragging: boolean;
  /** False under Reduced_Motion; callers should skip the height transition. */
  animate: boolean;
  handlers: SheetDragHandlers;
}

interface Gesture {
  pointerId: number;
  startY: number;
  startHeight: number;
  lastY: number;
  lastT: number;
  velocity: number; // px/ms, positive = growing
  height: number;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

/** Pointer-driven Bottom_Sheet drag that settles at the nearest Detent. */
export function useSheetDrag({ heights, detent, onDetentChange }: UseSheetDragOptions): SheetDrag {
  const reduced = useReducedMotion();
  const gesture = useRef<Gesture | null>(null);
  const [dragHeight, setDragHeight] = useState<number | null>(null);

  const settle = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      const g = gesture.current;
      if (!g || g.pointerId !== e.pointerId) return;
      gesture.current = null;
      try {
        if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
          e.currentTarget.releasePointerCapture(e.pointerId);
        }
      } catch {
        /* capture already lost */
      }
      setDragHeight(null);
      onDetentChange(nearestDetent(heights, g.height, g.velocity));
    },
    [heights, onDetentChange],
  );

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      if (e.button !== undefined && e.button > 0) return;
      const startHeight = heights[detent];
      gesture.current = {
        pointerId: e.pointerId,
        startY: e.clientY,
        startHeight,
        lastY: e.clientY,
        lastT: e.timeStamp,
        velocity: 0,
        height: startHeight,
      };
      try {
        e.currentTarget.setPointerCapture?.(e.pointerId);
      } catch {
        /* unsupported or pointer already gone */
      }
    },
    [heights, detent],
  );

  const onPointerMove = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      const g = gesture.current;
      if (!g || g.pointerId !== e.pointerId) return;
      const height = clamp(g.startHeight + (g.startY - e.clientY), heights.collapsed, heights.full);
      const dt = e.timeStamp - g.lastT;
      if (dt > 0) g.velocity = (g.lastY - e.clientY) / dt;
      g.lastY = e.clientY;
      g.lastT = e.timeStamp;
      g.height = height;
      setDragHeight(height);
    },
    [heights],
  );

  return {
    dragHeight,
    dragging: dragHeight !== null,
    animate: !reduced,
    handlers: { onPointerDown, onPointerMove, onPointerUp: settle, onPointerCancel: settle },
  };
}
