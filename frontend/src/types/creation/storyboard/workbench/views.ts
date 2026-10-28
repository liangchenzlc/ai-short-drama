import type { ShotRead } from '@host/types/creation/storyboard/shots';
import type { KeyboardEventHandler, ReactNode, RefObject } from 'react';

export type StoryboardEditorHeaderProps = {
  selectedShot: ShotRead | undefined;
  locked: boolean;
  selectionDisabled: boolean;
  editorTab: string;
  mediaTab: string;
  total: number | null;
  moreLoading: boolean;
  mediaSettingsOpen: boolean;
  mediaSettingsTrigger: RefObject<HTMLButtonElement | null>;
  onEditorTab: (tab: string) => void;
  onEditorKeyDown: KeyboardEventHandler<HTMLButtonElement>;
  onAdjacent: (direction: -1 | 1) => void;
  onHistory: () => void;
  onToggleSettings: () => void;
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
