import { StoryboardWorkspace } from '@host/features/creation/storyboard/workbench/StoryboardWorkspace';
import type { StoryboardWorkspaceProps } from '@host/types/creation/storyboard/workbench';
import './index.css';

export function StoryboardStage(props: StoryboardWorkspaceProps) {
  return <StoryboardWorkspace {...props} />;
}
