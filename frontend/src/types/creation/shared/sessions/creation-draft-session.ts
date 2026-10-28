import type { CreationDraftValue, UserCreationDraft } from '@host/types/creation/drafts/requests';

export type CreationSaveStatus = 'idle' | 'loading' | 'saved' | 'local' | 'unsaved' | 'saving' | 'error' | 'conflict';

export interface CreationDraftSnapshot {
  value: CreationDraftValue;
  loaded: boolean;
  status: CreationSaveStatus;
  message: string;
  saved: UserCreationDraft | null;
}

export interface DraftRecovery {
  row_version: string | null;
  value: CreationDraftValue;
  pending?: { version: string | null; value: CreationDraftValue } | null;
}

export interface DraftPersistence {
  read(): DraftRecovery | null;
  save(value: DraftRecovery): void;
  clear(): void;
  backup?(value: DraftRecovery): void;
}
