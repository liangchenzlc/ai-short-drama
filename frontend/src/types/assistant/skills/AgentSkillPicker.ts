import type { AgentSkill } from '@host/types/assistant/engine';

export interface Props {
  selected: AgentSkill[];
  disabled: boolean;
  dialogClassName?: string;
  layout?: 'standard';
  onChange: (skills: AgentSkill[]) => void;
  onClose: () => void;
}
