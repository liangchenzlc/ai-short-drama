import type { AssetDraft, LibraryAssetRead } from '@host/types/creation/assets/management';

export type AssetSaveStatus = 'saved' | 'dirty' | 'saving' | 'error' | 'conflict';

export interface AssetRecovery {
  base: LibraryAssetRead;
  fields: AssetDraft;
  pending?: AssetDraft | null;
}

export interface AssetRecoveryStorage {
  read: () => AssetRecovery | null;
  save: (value: AssetRecovery) => void;
  clear: () => void;
  backup?: (value: AssetRecovery) => void;
}

export interface AssetEditingSnapshot {
  asset: LibraryAssetRead;
  saved: LibraryAssetRead;
  status: AssetSaveStatus;
  message: string;
  dirty: boolean;
  recoveryBlocked: boolean;
}
