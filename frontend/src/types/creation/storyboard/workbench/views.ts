import type { ShotRead } from '@host/types/creation/storyboard/shots';
import type { ReactNode, RefObject } from 'react';

export type StoryboardEditorHeaderProps = {
  selectedShot: ShotRead | undefined;
  children: ReactNode;
};

export type StoryboardShotActionsProps = {
  readOnly: boolean;
  selectedShot: ShotRead | undefined;
  batchEnabled: boolean;
  batchMode: boolean;
  total: number | null;
  open: boolean;
  selectionDisabled: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
  onOpenChange: (open: boolean) => void;
  onAction: (action: 'up' | 'down' | 'batch') => void;
};
