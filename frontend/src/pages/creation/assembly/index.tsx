import '@host/features/creation/assembly/workbench/assembly.css';
import { AssemblyStageContent } from '@host/features/creation/assembly/workbench/AssemblyStageContent';
import '@host/features/creation/assembly/workbench/standard-assembly.css';
import type { AssemblyStageProps } from '@host/types/creation/assembly/workbench/stage';
import './index.css';

export function AssemblyStage(props: AssemblyStageProps) {
  return <AssemblyStageContent {...props} />;
}
