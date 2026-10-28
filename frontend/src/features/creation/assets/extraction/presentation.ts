import type { AssetKind } from '@host/types/creation/assets/management';
export const labels: Record<AssetKind, string> = {
  character: '角色',
  scene: '场景',
  prop: '道具',
};
export const importanceLabels = {
  core: '推动剧情',
  continuity: '维持连续性',
} as const;
export const allKinds = Object.keys(labels) as AssetKind[];
