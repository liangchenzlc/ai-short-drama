import type { AgentConversation, ConversationScope } from '../../api/types/agents';

export interface AgentSubject { type: 'asset' | 'shot'; id: string; label: string; revision?: string }

export function episodeConversationScope(stage: ConversationScope['stage'], episodeId: string, subject?: AgentSubject | null): ConversationScope {
  const scoped = stage === 'assets' && subject?.type === 'asset' || stage === 'storyboard' && subject?.type === 'shot';
  return {
    stage, subject_type: scoped ? subject.type : 'episode', subject_id: scoped ? subject.id : episodeId,
    task_type: scoped ? 'creation' : stage === 'source' ? 'writing' : stage === 'assets' ? 'extraction' : 'planning',
  };
}

export function conversationScopeKey(scope: ConversationScope): string {
  return `${scope.stage}:${scope.subject_type}:${scope.subject_id}:${scope.task_type}`;
}

export function conversationMatchesScope(conversation: AgentConversation, scope: ConversationScope): boolean {
  return conversation.scope_version === 1 && conversation.stage === scope.stage && conversation.subject_type === scope.subject_type
    && conversation.subject_id === scope.subject_id && conversation.task_type === scope.task_type;
}

export function scopeConversation(search: string, scope: ConversationScope): string | undefined {
  const parameters = new URLSearchParams(search);
  return parameters.get(`conversation_${conversationScopeKey(scope)}`) || (scope.subject_type === 'episode'
    ? parameters.get(`conversation_${scope.stage}`) || ((!parameters.has('conversation_stage') || parameters.get('conversation_stage') === scope.stage) ? parameters.get('conversation') || undefined : undefined)
    : undefined);
}

export function selectScopeConversation(search: string, scope: ConversationScope, id?: string): URLSearchParams {
  const parameters = new URLSearchParams(search);
  parameters.set('mode', 'agent'); parameters.set('conversation_stage', scope.stage);
  for (const key of ['conversation', `conversation_${scope.stage}`, `conversation_${conversationScopeKey(scope)}`]) {
    if (id) parameters.set(key, id); else parameters.delete(key);
  }
  return parameters;
}
