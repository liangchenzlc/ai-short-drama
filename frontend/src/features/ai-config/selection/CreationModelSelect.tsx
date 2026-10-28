import { ConfigSelect } from '@host/features/ai-config/selection/ModelSelect';
import { savedConfigId } from '@host/features/ai-config/selection/config-selection';
import type { ServiceType } from '@host/types/ai-config/editor/config-model';
import { useParams } from 'react-router-dom';
export function CreationModelSelect({
  kind,
  value,
  onChange,
  onResolvedChange,
  disabled,
  label,
  optionLayout,
  presentation,
  feedbackContainer,
}: {
  kind: ServiceType;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  label: string;
  onResolvedChange?: (value: string | undefined) => void;
  optionLayout?: 'single' | 'detailed' | 'compact';
  presentation?: 'standard-creation';
  feedbackContainer?: HTMLElement | null;
}) {
  const { projectId = 'default' } = useParams();
  return (
    <ConfigSelect
      kind={kind}
      preferenceKey={`project:${projectId}:${kind}:${label}`}
      value={savedConfigId(value)}
      onChange={(id) => onChange(id ?? '')}
      onResolvedChange={onResolvedChange}
      disabled={disabled}
      label={label}
      optionLayout={optionLayout}
      presentation={presentation}
      feedbackContainer={feedbackContainer}
    />
  );
}
