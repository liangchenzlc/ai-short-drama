import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';

export interface AssemblyStageProps {
  projectId: string;
  readOnly: boolean;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  onStoryboard: () => void;
}
