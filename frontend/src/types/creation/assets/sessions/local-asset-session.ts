import type { AssetDraft, LibraryAssetRead } from '@host/types/creation/assets/management';
import type { LocalAssetDraft } from '@host/types/creation/assets/sessions/local-asset-draft-model';
import type { CreationDraftValue } from '@host/types/creation/drafts/requests';

export interface LocalAssetSnapshot {
  drafts: LocalAssetDraft[];
  created: Record<string, { asset: LibraryAssetRead; generation?: CreationDraftValue }>;
  status: 'saved' | 'unsaved' | 'saving' | 'error';
  message: string;
  recoveryError: string;
}

export interface LocalAssetTransport {
  create(body: AssetDraft, key: string): Promise<LibraryAssetRead>;
  preserveCreated(saved: LibraryAssetRead, current: LocalAssetDraft): void;
}
