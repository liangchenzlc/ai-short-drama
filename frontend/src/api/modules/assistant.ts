import { http } from '../http';
import type { AgentAttachment, AgentConversationPage, AgentMessagePage, AgentRun, AgentRunPage, AgentSendResult, ConversationRuntimeState, UpdateAgentConversation } from '../types/agents';
import type { AssistantConversation, AssistantConversationPage, AssistantSendInput } from '../types/assistant';
import type { Page } from '../types/generations';

const root = '/assistant';
const conversationPath = (id: string) => `${root}/conversations/${encodeURIComponent(id)}`;

export const assistantApi = {
  async conversations(projectId: string, offset = 0, includeArchived = false, signal?: AbortSignal, query = '') {
    return (await http.get<AssistantConversationPage>(`${root}/conversations`, { params: { project_id: projectId, offset, limit: 20, include_archived: includeArchived, query: query.trim() || undefined }, signal })).data;
  },
  async legacyConversations(projectId: string, offset = 0, signal?: AbortSignal, query = '') {
    return (await http.get<AgentConversationPage>(`${root}/legacy-conversations`, { params: { project_id: projectId, offset, limit: 20, query: query.trim() || undefined }, signal })).data;
  },
  async resolveConversation(projectId: string, signal?: AbortSignal) {
    return (await http.post<AssistantConversation>(`${root}/conversations/resolve`, { project_id: projectId }, { signal })).data;
  },
  async createConversation(projectId: string, key: string) {
    return (await http.post<AssistantConversation>(`${root}/conversations`, { project_id: projectId }, { headers: { 'Idempotency-Key': key } })).data;
  },
  async conversation(id: string, signal?: AbortSignal) {
    return (await http.get<AssistantConversation>(conversationPath(id), { signal })).data;
  },
  async updateConversation(id: string, body: UpdateAgentConversation) {
    return (await http.patch<AssistantConversation>(conversationPath(id), body)).data;
  },
  async messages(id: string, offset = 0, signal?: AbortSignal) {
    return (await http.get<AgentMessagePage>(`${conversationPath(id)}/messages`, { params: { offset, limit: 50 }, signal })).data;
  },
  async state(id: string, signal?: AbortSignal) {
    return (await http.get<ConversationRuntimeState>(`${conversationPath(id)}/state`, { signal })).data;
  },
  async runs(id: string, signal?: AbortSignal) {
    return (await http.get<AgentRunPage>(`${conversationPath(id)}/runs`, { params: { offset: 0, limit: 1 }, signal })).data;
  },
  async run(id: string, signal?: AbortSignal) {
    return (await http.get<AgentRun>(`${root}/runs/${encodeURIComponent(id)}`, { signal })).data;
  },
  async send(id: string, body: AssistantSendInput, key: string) {
    return (await http.post<AgentSendResult>(`${conversationPath(id)}/messages`, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async stop(id: string) {
    return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/stop`, {})).data;
  },
  async attachments(id: string, offset = 0, signal?: AbortSignal) {
    return (await http.get<Page<AgentAttachment>>(`${conversationPath(id)}/attachments`, { params: { offset, limit: 50, pending: true }, signal })).data;
  },
  async uploadAttachment(id: string, file: File, key: string) {
    const body = new FormData(); body.append('file', file);
    return (await http.post<AgentAttachment>(`${conversationPath(id)}/attachments/uploads`, body, { headers: { 'Idempotency-Key': key }, timeout: 120_000 })).data;
  },
  async referenceAttachment(id: string, source_type: 'media' | 'asset', source_id: string, key: string) {
    return (await http.post<AgentAttachment>(`${conversationPath(id)}/attachments/references`, { source_type, source_id }, { headers: { 'Idempotency-Key': key } })).data;
  },
  async removeAttachment(id: string, attachmentId: string) {
    await http.delete(`${conversationPath(id)}/attachments/${encodeURIComponent(attachmentId)}`);
  },
};
