import type { AssetDraft, AssetKind } from '@host/types/creation/assets/management';
import type { CreationDraftValue } from '@host/types/creation/drafts/requests';

export interface LocalAssetDraft extends Omit<AssetDraft, 'kind'> {
  id: string;
  kind: AssetKind | null;
  status: 'local' | 'saving' | 'error' | 'unknown';
  message: string;
  attempt: { key: string; body: AssetDraft } | null;
  generation?: CreationDraftValue;
}
