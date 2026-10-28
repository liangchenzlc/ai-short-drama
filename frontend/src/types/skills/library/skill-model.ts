export type SkillModeChoice = 'general' | 'standard' | 'infinite_canvas';

export interface SkillFileNode {
  key: string;
  title: string;
  isLeaf: boolean;
  children?: SkillFileNode[];
}
