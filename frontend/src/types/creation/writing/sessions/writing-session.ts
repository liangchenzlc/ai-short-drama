import type { WritingResponse, WritingTask } from '@host/types/creation/writing/documents';

export type WritingField = 'script';

export type Field = WritingField;

export type Status = 'loading' | 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict';

export interface WritingSnapshot {
  loaded: boolean;
  status: Status;
  script: string;
  scriptId: string | null;
  contentVersion: string;
  confirmed: boolean;
  dirty: boolean;
  busy: boolean;
  message: string;
  activeWritingTasks: WritingTask[];
  recoverable?: boolean;
  recoveryChanged?: boolean;
  recoveryBlocked?: boolean;
}

export interface WritingDraft {
  base_version: string;
  script: string;
  script_id: string | null;
  pending?: Attempt | null;
  script_change_source?: 'import';
}

export interface WritingRecovery {
  read: () => WritingDraft | null;
  save: (draft: WritingDraft) => void;
  clear: () => void;
  export?: () => string | null;
}

export type Attempt = {
  before: WritingResponse;
  field: Field;
  content: string;
  changeSource?: 'import';
};
