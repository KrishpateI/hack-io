/**
 * Pure, canvas-free marker icon generator. Each status uses a different SHAPE (and filled vs
 * hollow outline) so meaning never relies on color alone (Req 36.2).
 */
export type IconShape = 'circle' | 'triangle' | 'diamond' | 'square' | 'cross' | 'star' | 'hexagon';

export interface IconImage {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

export interface IconSpec {
  /** MapLibre image id. */
  id: string;
  shape: IconShape;
  color: string;
  /** Hollow icons only draw the outline. */
  hollow?: boolean;
}

type Pt = [number, number];

function ngon(n: number, r: number, rot: number): Pt[] {
  return Array.from({ length: n }, (_, i) => {
    const a = rot + (i * 2 * Math.PI) / n;
    return [r * Math.cos(a), r * Math.sin(a)] as Pt;
  });
}

function star(): Pt[] {
  const pts: Pt[] = [];
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? 1 : 0.45;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    pts.push([r * Math.cos(a), r * Math.sin(a)]);
  }
  return pts;
}

function xCross(): Pt[] {
  const w = 0.22;
  const a = 1;
  // 12-vertex "X" polygon.
  return [
    [-a, -a + w * 2], [-a + w * 2, -a], [0, -w * 2], [a - w * 2, -a], [a, -a + w * 2],
    [w * 2, 0], [a, a - w * 2], [a - w * 2, a], [0, w * 2], [-a + w * 2, a], [-a, a - w * 2],
    [-w * 2, 0],
  ];
}

const POLYGONS: Record<IconShape, Pt[] | null> = {
  circle: null,
  triangle: ngon(3, 1, -Math.PI / 2),
  diamond: ngon(4, 1, 0),
  square: [[-0.85, -0.85], [0.85, -0.85], [0.85, 0.85], [-0.85, 0.85]],
  cross: xCross(),
  star: star(),
  hexagon: ngon(6, 1, 0),
};

function inPolygon(poly: Pt[], x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Is the normalized point (-1..1) inside the shape? Exported for tests. */
export function insideShape(shape: IconShape, x: number, y: number): boolean {
  const poly = POLYGONS[shape];
  if (!poly) return Math.hypot(x, y) <= 1;
  return inPolygon(poly, x, y);
}

const inside = insideShape;

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.replace(/./g, (c) => c + c) : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const OUTLINE: [number, number, number] = [11, 18, 32]; // palette.base, contrast ring

/** Render an icon as RGBA pixels. `size` is in device pixels. */
export function renderIcon(spec: IconSpec, size = 32): IconImage {
  const data = new Uint8ClampedArray(size * size * 4);
  const [r, g, b] = hexToRgb(spec.color);
  const scale = (size / 2 - 1) / (size / 2);
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const x = ((px + 0.5) / size) * 2 - 1;
      const y = ((py + 0.5) / size) * 2 - 1;
      const sx = x / scale;
      const sy = y / scale;
      if (!inside(spec.shape, sx, sy)) continue;
      const core = inside(spec.shape, sx / 0.62, sy / 0.62); // inner area
      const ring = !inside(spec.shape, sx / 0.8, sy / 0.8); // outer edge band
      const i = (py * size + px) * 4;
      let rgb: [number, number, number] | null;
      if (ring) rgb = spec.hollow ? [r, g, b] : OUTLINE;
      else if (spec.hollow) rgb = core ? null : [r, g, b];
      else rgb = [r, g, b];
      if (!rgb) continue;
      data[i] = rgb[0];
      data[i + 1] = rgb[1];
      data[i + 2] = rgb[2];
      data[i + 3] = 255;
    }
  }
  return { width: size, height: size, data };
}
