import type { ReactNode } from 'react';
import type { AssemblyState, RenderJob } from '@host/types/creation/assembly/document';

export interface AssemblyDialogProps {
  busy: boolean;
  feedback: ReactNode;
  onClose: () => void;
}

export interface AssemblySyncDialogProps extends AssemblyDialogProps {
  changes?: AssemblyState['changes'];
  onSubmit: () => Promise<void>;
}

export interface AssemblyExportDialogProps extends AssemblyDialogProps {
  value: AssemblyState | null;
  disabled: boolean;
  includedCount: number;
  total: number;
  blocked: boolean;
  stale: boolean;
  onAspect: (aspect: 'auto' | '16:9' | '9:16') => void;
  onResolution: (resolution: '720p' | '1080p') => void;
  onSubmit: (acknowledge: boolean) => Promise<void>;
}

export interface AssemblyExportHistoryProps extends AssemblyDialogProps {
  history: RenderJob[];
  displayedHistory?: RenderJob[];
  moreHistory: boolean;
  disabled: boolean;
  active: boolean;
  currentMediaId?: string | null;
  download: (id: string) => string;
  onPlay: (job: RenderJob) => void;
  onApply: (job: RenderJob) => Promise<void>;
  onRetry: (job: RenderJob) => Promise<void>;
  onCancel: (job: RenderJob) => Promise<void>;
  onRefresh: () => Promise<void>;
  onMore: () => Promise<void>;
}

export interface AssemblyAdoptionDialogProps extends AssemblyDialogProps {
  applying: RenderJob;
  onSubmit: () => Promise<void>;
}
