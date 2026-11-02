import type { TaskMediaLimits } from '@host/types/assistant/api';
export interface MediaLimitFieldsProps {
  value: TaskMediaLimits;
  onChange: (value: TaskMediaLimits) => void;
  disabled?: boolean;
  prefix?: string;
}
