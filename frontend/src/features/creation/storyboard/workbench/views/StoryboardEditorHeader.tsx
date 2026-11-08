import type { StoryboardEditorHeaderProps } from '@host/types/creation/storyboard/workbench/views';

export function StoryboardEditorHeader({ selectedShot, children }: StoryboardEditorHeaderProps) {
  return (
    <header className="storyboardViewHeader">
      <h2>{selectedShot ? `分镜 ${String(selectedShot.position).padStart(2, '0')}` : '分镜'}</h2>
      {children}
    </header>
  );
}
