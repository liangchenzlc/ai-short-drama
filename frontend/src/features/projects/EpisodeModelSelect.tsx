import { ConfigSelect } from '../generations/ConfigSelect';
import { savedConfigId } from '../ai-config/config-selection';
import type { ServiceType } from '../ai-config/config-model';
import { useParams } from 'react-router-dom';

export function EpisodeModelSelect({ kind, value, onChange, onResolvedChange, disabled, label }: {
  kind: ServiceType; value: string; onChange: (value: string) => void; disabled: boolean; label: string;
  onResolvedChange?: (value: string | undefined) => void;
}) {
  const { episodeId = 'default' } = useParams();
  return <ConfigSelect kind={kind} preferenceKey={`episode:${episodeId}:${kind}:${label}`} value={savedConfigId(value)} onChange={(id) => onChange(id ?? '')} onResolvedChange={onResolvedChange} disabled={disabled} label={label} />;
}
