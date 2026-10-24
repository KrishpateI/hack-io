import type { Feature, Polygon } from 'geojson';

/** Chicago_Extraction_Area (Req 2.3). */
export const EXTRACTION_BOUNDS = { north: 41.91, south: 41.85, west: -87.68, east: -87.6 } as const;

export const EXTRACTION_LABEL = 'Data extraction boundary, not an official evacuation boundary';

/** Downtown Chicago, [lng, lat]. */
export const CHICAGO_CENTER: [number, number] = [-87.6298, 41.8781];

export function extractionAreaFeature(): Feature<Polygon> {
  const { north, south, west, east } = EXTRACTION_BOUNDS;
  return {
    type: 'Feature',
    properties: { label: EXTRACTION_LABEL },
    geometry: {
      type: 'Polygon',
      coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    },
  };
}
