import { ConfigSelect } from '../generations/ConfigSelect';
import { savedConfigId } from '../ai-config/config-selection';
import type { ServiceType } from '../ai-config/config-model';

export function EpisodeModelSelect({ kind, value, onChange, onResolvedChange, disabled, label }: {
  kind: ServiceType; value: string; onChange: (value: string) => void; disabled: boolean; label: string;
  onResolvedChange?: (value: string | undefined) => void;
}) {
  return <ConfigSelect kind={kind} value={savedConfigId(value)} onChange={(id) => onChange(id ?? '')} onResolvedChange={onResolvedChange} disabled={disabled} label={label} />;
}
