import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';

export type AssetReferenceChange = {
  before: readonly AssetPromptReference[];
  after: readonly AssetPromptReference[];
};
