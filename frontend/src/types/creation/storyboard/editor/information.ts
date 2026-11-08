import type { ShotRead } from '@host/types/creation/storyboard/shots';
export interface ShotInformationProps {
  shot: ShotRead;
  disabled: boolean;
  reloading: boolean;
  hasPrevious: boolean;
  status: string | undefined;
  onChange: (patch: Partial<Pick<ShotRead, 'title' | 'shot_size' | 'script' | 'details' | 'duration_ms'>>) => void;
  onDownloadRecovery: () => void;
  onRetry: () => Promise<boolean>;
  onReload: (keepLocal?: boolean) => Promise<void>;
}
