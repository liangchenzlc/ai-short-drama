import type { GenerationSummary } from '@host/types/generations/api';
import type { ReactNode } from 'react';

export interface StoryboardHistoryProps {
  projectId: string;
  busy: boolean;
  candidate: GenerationSummary | null;
  setCandidate: (task: GenerationSummary | null) => void;
  readOnly: boolean;
  loading: boolean;
  loadError: string;
  hasPage: boolean;
  message: string;
  setMessage: (message: string) => void;
  applyResult: (mode: 'append' | 'replace') => Promise<void>;
  tasksLoading: boolean;
  tasks: GenerationSummary[];
  tasksError: string;
  setTaskRevision: (update: (value: number) => number) => void;
  tasksLoaded: boolean;
  setTaskDetailId: (id: string) => void;
  tasksTotal: number;
  loadMoreTasks: () => Promise<void>;
  renderShotActions: (origin: 'main' | 'history') => ReactNode;
  setActionsOrigin: (origin: 'main' | 'history' | null) => void;
  setHistoryOpen: (open: boolean) => void;
}
