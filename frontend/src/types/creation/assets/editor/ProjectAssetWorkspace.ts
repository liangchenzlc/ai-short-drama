import type { AssetScope } from '@host/types/creation/assets/management';

export type ProjectAssetScope = Extract<AssetScope, { kind: 'project' }>;
