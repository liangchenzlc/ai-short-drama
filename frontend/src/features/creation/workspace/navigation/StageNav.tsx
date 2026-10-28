import type { CreationStage } from '@host/types/creation/workspace/model';
export const creationStages: { id: CreationStage; label: string }[] = [
  { id: 'source', label: '剧本创作' },
  { id: 'assets', label: '资产生成' },
  { id: 'storyboard', label: '分镜制作' },
  { id: 'assembly', label: '成片预览' },
];
