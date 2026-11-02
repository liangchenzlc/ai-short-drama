import type { TaskMediaLimits } from '@host/types/assistant/api';

export interface TaskMediaLimitsProps {
  value: TaskMediaLimits | undefined;
  frozen?: TaskMediaLimits;
  onChange: (value: TaskMediaLimits | undefined) => void;
  disabled: boolean;
}
