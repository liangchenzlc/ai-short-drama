import type { AgentSkill, AgentSkillContent, AgentSkillDetail, AgentSkillResource } from '@host/types/assistant/engine';

export type SkillResourceDraft = Pick<AgentSkillResource, 'path' | 'kind'> & {
  content: string | null;
  origin_version?: string;
  checksum_sha256?: string;
  encoding?: 'utf-8' | 'base64';
};

export type SkillFormValues = Required<Omit<AgentSkillContent, 'resources'>> & {
  resources: SkillResourceDraft[];
};

export interface SkillSubmission {
  key: string;
  body: AgentSkillContent;
  row_version: string | null;
}

export interface SkillDraft {
  schema_version: 1;
  key: string;
  base: AgentSkillDetail | null;
  values: SkillFormValues;
  changed: boolean;
  pending: SkillSubmission | null;
  conflict: boolean;
  imported?: boolean;
  tags_provided?: boolean;
}

export type SkillPendingAction =
  | { kind: 'copy'; item: AgentSkill; key: string }
  | { kind: 'settings'; item: AgentSkill; enabled: boolean }
  | { kind: 'mode'; item: AgentSkill; modes: AgentSkill['modes'] }
  | { kind: 'delete'; item: AgentSkill };

export type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
