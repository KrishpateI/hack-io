import type { Incident } from '../services/api';
import { DISASTERS, type IncidentType } from '../components/sidebar/disasters';
import { DisasterSelector } from '../components/sidebar/DisasterSelector';
import { SimulatedBadge } from '../components/SimulatedBadge';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';

export interface DisasterSelectionScreenProps {
  selectedType: IncidentType | null;
  incidents: Incident[];
  selectedIncidentId: string | null;
  loading?: boolean;
  error?: string | null;
  onSelectType: (t: IncidentType) => void;
  onSelectIncident: (i: Incident) => void;
  onClose: () => void;
}

/** Full-screen disaster selector with every scenario described (Req 34.6). */
export function DisasterSelectionScreen({ onClose, ...selector }: DisasterSelectionScreenProps) {
  return (
    <ScreenFrame title={screenTitle('disaster-selection')} onClose={onClose} testId="screen-disaster-selection">
      <p className="text-sm text-text-muted">
        <SimulatedBadge /> Demonstration scenarios use simulated data and are labeled SIMULATED. They are not real events or orders.
      </p>
      <section aria-labelledby="disaster-descriptions-h">
        <h2 id="disaster-descriptions-h" className="mb-2 text-base font-semibold">How each disaster changes routing</h2>
        <dl className="space-y-2">
          {DISASTERS.map((d) => (
            <div key={d.type} className="rounded border border-border-subtle p-2">
              <dt className="font-semibold">{d.label}</dt>
              <dd className="text-sm text-text-muted">{d.routing}</dd>
            </div>
          ))}
        </dl>
      </section>
      <div className="rounded border border-border-subtle">
        <DisasterSelector {...selector} />
      </div>
    </ScreenFrame>
  );
}
