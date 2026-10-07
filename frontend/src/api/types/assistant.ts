import type { AgentConversation, AgentSendInput } from './agents';

export interface AssistantConversation extends Omit<AgentConversation, 'episode_id'> {
  episode_id: null;
  scope_version: 2;
}

export interface AssistantConversationPage {
  items: AssistantConversation[];
  total: number;
  offset: number;
  limit: number;
}

export interface AssistantMessageContext {
  kind: 'episode' | 'canvas';
  id: string;
  revision: string;
  include_document?: boolean;
  stage?: 'source' | 'assets' | 'storyboard' | 'assembly';
  storyboard_revision?: string;
  selected?: { kind: 'asset' | 'shot' | 'node'; id: string; revision?: string }[];
}

export type AssistantReference = NonNullable<AssistantMessageContext['selected']>[number];
export interface AssistantMentionReference extends AssistantReference { name: string }

export interface AssistantSendInput extends Pick<AgentSendInput, 'content' | 'model_config_id' | 'attachment_ids' | 'skills' | 'video_audio'> {
  context?: AssistantMessageContext | null;
}
