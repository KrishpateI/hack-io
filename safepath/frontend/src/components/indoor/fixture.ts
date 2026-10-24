import type { IndoorFeature, IndoorGeoJson } from './indoorModel';

/** Test fixture mirroring GET /api/indoor/building (data/demo/indoor_building.json). */
const A = { lat: 41.883, lon: -87.6345 };
const ll = (x: number, y: number): number[] => [
  A.lon + x / (111_320 * Math.cos((A.lat * Math.PI) / 180)),
  A.lat + y / 111_320,
];
const nid = (lvl: string, key: string) => `sim-indoor-${lvl}-${key}`;
const P: Record<string, [number, number]> = {
  'cor-W': [10, 30], 'cor-C': [40, 30], 'cor-E': [70, 30],
  'nor-W': [10, 50], 'nor-C': [40, 50], 'nor-E': [70, 50],
};

export function demoBuilding(): IndoorGeoJson {
  const feats: IndoorFeature[] = [];
  const add = (geometry: IndoorFeature['geometry'], properties: Record<string, unknown>) =>
    feats.push({ type: 'Feature', geometry, properties: { ...properties, simulated: true } });
  for (const [lvl, n] of [['L1', 1], ['L2', 2], ['L3', 3]] as const) {
    add({ type: 'Polygon', coordinates: [[ll(0, 0), ll(80, 0), ll(80, 60), ll(0, 60), ll(0, 0)]] }, { role: 'floor', level: lvl, floor: n, name: `SIMULATED Floor ${n}` });
    for (const [id, path] of [['corridor-1', ['cor-W', 'cor-C', 'cor-E']], ['corridor-2', ['nor-W', 'nor-C', 'nor-E']]] as const) {
      add({ type: 'LineString', coordinates: path.map((k) => ll(...P[k])) }, {
        role: 'corridor', level: lvl, corridor_id: id, name: `SIMULATED ${id}`, node_ids: path.map((k) => nid(lvl, k)),
      });
    }
    add({ type: 'Point', coordinates: ll(20, 10) }, { role: 'zone', level: lvl, zone_id: 'zone-1', node_id: nid(lvl, 'zone-1'), name: 'SIMULATED Zone 1' });
    add({ type: 'Point', coordinates: ll(60, 10) }, { role: 'zone', level: lvl, zone_id: 'zone-2', node_id: nid(lvl, 'zone-2'), name: 'SIMULATED Zone 2' });
    for (const [id, x, y] of [['A', 10, 20], ['B', 40, 40], ['C', 70, 20]] as const) {
      add({ type: 'Point', coordinates: ll(x, y) }, { role: 'stairwell', level: lvl, stairwell_id: id, node_id: nid(lvl, `stair-${id}`), name: `SIMULATED Stairwell ${id}` });
    }
    if (lvl === 'L1') {
      add({ type: 'Point', coordinates: ll(0, 30) }, { role: 'exit', level: lvl, exit_id: 'exit-1', node_id: nid(lvl, 'exit-1'), name: 'SIMULATED Exit 1' });
      add({ type: 'Point', coordinates: ll(80, 30) }, { role: 'exit', level: lvl, exit_id: 'exit-2', node_id: nid(lvl, 'exit-2'), name: 'SIMULATED Exit 2' });
    }
  }
  add({ type: 'Point', coordinates: ll(60, 10) }, { role: 'start_position', level: 'L3', start_id: 'start-f3-zone-2', node_id: nid('L3', 'zone-2'), name: 'SIMULATED Floor 3, Zone 2' });
  add({ type: 'Point', coordinates: ll(10, 30) }, { role: 'start_position', level: 'L3', start_id: 'start-f3-west-corridor', node_id: nid('L3', 'cor-W'), name: 'SIMULATED Floor 3, west corridor' });
  return {
    type: 'FeatureCollection',
    name: 'SIMULATED Demo Building',
    properties: { notice: 'SIMULATED fictional building. Not a real building and not a real UIC floor plan.' },
    features: feats,
  };
}

/** Indoor edge ids of a route from Floor 3 west corridor via Stairwell A down to Exit 1. */
export const ROUTE_EDGES = [
  'sim-indoor-e-L3-cor-W-cor-C',
  'sim-indoor-e-L3-cor-C-nor-C',
  'sim-indoor-t-stair-B-L3-L2',
  'sim-indoor-t-stair-B-L2-L1',
  'sim-indoor-e-L1-stair-B-cor-C',
  'sim-indoor-e-L1-cor-C-cor-W',
  'sim-indoor-e-L1-cor-W-exit-1',
  'sim-indoor-t-exit-1-out',
];
