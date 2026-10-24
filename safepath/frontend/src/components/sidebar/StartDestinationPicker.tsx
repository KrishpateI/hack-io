import { useState } from 'react';
import { EXTRACTION_BOUNDS } from '../../maps/extractionArea';

export interface LatLng {
  lat: number;
  lng: number;
}
export interface Destination extends LatLng {
  /** True for demo destinations; these are never official shelters. */
  simulated?: boolean;
}

export const SIM_DESTINATION_LABEL = 'SIMULATION DESTINATION — NOT an official evacuation shelter';

/** Sample points for the flood scenario (inside the extraction area; simulation only). */
export const SAMPLE_FLOOD_POINTS: { start: LatLng; destination: Destination } = {
  start: { lat: 41.8689, lng: -87.6486 },
  destination: { lat: 41.8827, lng: -87.6233, simulated: true },
};

/** Returns an error message or null when the coordinate pair is valid and in bounds. */
export function validateCoordinates(latText: string, lngText: string): string | null {
  const lat = Number(latText);
  const lng = Number(lngText);
  if (latText.trim() === '' || lngText.trim() === '' || !Number.isFinite(lat) || !Number.isFinite(lng)) {
    return 'Enter numeric latitude and longitude.';
  }
  const { north, south, west, east } = EXTRACTION_BOUNDS;
  if (lat < south || lat > north || lng < west || lng > east) {
    return `Outside the Chicago extraction area (latitude ${south} to ${north}, longitude ${west} to ${east}).`;
  }
  return null;
}

function CoordinateField({ id, legend, value, onValid }: { id: string; legend: string; value: LatLng | null; onValid: (p: LatLng) => void }) {
  const [lat, setLat] = useState(value ? String(value.lat) : '');
  const [lng, setLng] = useState(value ? String(value.lng) : '');
  const [error, setError] = useState<string | null>(null);
  const commit = (la: string, ln: string) => {
    if (la.trim() === '' && ln.trim() === '') return setError(null);
    const e = validateCoordinates(la, ln);
    setError(e);
    if (!e) onValid({ lat: Number(la), lng: Number(ln) });
  };
  return (
    <fieldset className="mb-2 border-0 p-0">
      <legend className="text-sm font-medium">{legend}</legend>
      <div className="mt-1 flex gap-2">
        <label htmlFor={`${id}-lat`} className="flex-1 text-xs">Latitude
          <input id={`${id}-lat`} inputMode="decimal" value={lat} aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-err` : undefined}
            onChange={(e) => setLat(e.target.value)} onBlur={() => commit(lat, lng)}
            className="mt-0.5 w-full rounded border border-border-subtle bg-surface px-2 py-1 text-sm text-text-primary focus:outline-2 focus:outline-route" />
        </label>
        <label htmlFor={`${id}-lng`} className="flex-1 text-xs">Longitude
          <input id={`${id}-lng`} inputMode="decimal" value={lng} aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-err` : undefined}
            onChange={(e) => setLng(e.target.value)} onBlur={() => commit(lat, lng)}
            className="mt-0.5 w-full rounded border border-border-subtle bg-surface px-2 py-1 text-sm text-text-primary focus:outline-2 focus:outline-route" />
        </label>
      </div>
      {error && <p id={`${id}-err`} role="alert" className="mt-1 text-xs text-closure">Error: {error}</p>}
    </fieldset>
  );
}

interface Props {
  start: LatLng | null;
  destination: Destination | null;
  pickMode: 'start' | 'destination' | null;
  onPickModeChange: (m: 'start' | 'destination' | null) => void;
  onStartChange: (p: LatLng) => void;
  onDestinationChange: (p: Destination) => void;
  showSamplePoints?: boolean;
}

export function StartDestinationPicker({ start, destination, pickMode, onPickModeChange, onStartChange, onDestinationChange, showSamplePoints }: Props) {
  const pick = (m: 'start' | 'destination') => onPickModeChange(pickMode === m ? null : m);
  return (
    <section aria-labelledby="route-points-h" data-testid="start-destination-picker" className="p-3">
      <h2 id="route-points-h" className="mb-2 text-sm font-semibold text-text-primary">Start and destination</h2>
      <CoordinateField key={`s${start?.lat},${start?.lng}`} id="start" legend="Start" value={start} onValid={onStartChange} />
      <button type="button" aria-pressed={pickMode === 'start'} onClick={() => pick('start')}
        className="mb-3 min-h-11 rounded border border-border-subtle px-3 text-sm focus:outline-2 focus:outline-route">
        {pickMode === 'start' ? 'Click the map to set start (active)' : 'Pick start on map'}
      </button>
      <CoordinateField key={`d${destination?.lat},${destination?.lng}`} id="dest" legend="Destination" value={destination} onValid={(p) => onDestinationChange({ ...p })} />
      <button type="button" aria-pressed={pickMode === 'destination'} onClick={() => pick('destination')}
        className="mb-2 min-h-11 rounded border border-border-subtle px-3 text-sm focus:outline-2 focus:outline-route">
        {pickMode === 'destination' ? 'Click the map to set destination (active)' : 'Pick destination on map'}
      </button>
      {destination?.simulated && (
        <p data-testid="sim-destination-label" className="rounded border border-route-alt p-1 text-xs font-bold text-route-alt">{SIM_DESTINATION_LABEL}</p>
      )}
      {showSamplePoints && (
        <button type="button" onClick={() => { onStartChange(SAMPLE_FLOOD_POINTS.start); onDestinationChange(SAMPLE_FLOOD_POINTS.destination); }}
          className="mt-2 min-h-11 rounded border border-route px-3 text-sm text-route focus:outline-2 focus:outline-route">
          Use sample points (flood scenario)
        </button>
      )}
    </section>
  );
}
