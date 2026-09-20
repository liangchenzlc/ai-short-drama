import { ConfigSelect } from '../generations/ConfigSelect';
import { savedConfigId } from '../ai-config/config-selection';
import type { ServiceType } from '../ai-config/config-model';

export function EpisodeModelSelect({ kind, value, onChange, disabled, label }: {
  kind: ServiceType; value: string; onChange: (value: string) => void; disabled: boolean; label: string;
}) {
  return <ConfigSelect kind={kind} value={savedConfigId(value)} onChange={(id) => onChange(id ?? '')} disabled={disabled} label={label} />;
}
