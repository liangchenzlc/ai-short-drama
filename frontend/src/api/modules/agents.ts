import { http } from '../http';
import type { AgentAvailability, AgentConversation, AgentConversationPage, CreateAgentConversation, UpdateAgentConversation,
  AgentModel, AgentModelPage, AgentMessagePage, AgentRun, AgentRunPage, AgentSendInput, AgentSendResult, AgentReview, AgentAttachment, AgentSkill, ConversationScope, ConversationRuntimeState } from '../types/agents';
import type { Page } from '../types/generations';

const root = '/agent';
const conversationPath = (id: string) => `${root}/conversations/${encodeURIComponent(id)}`;

export const agentsApi = {
  async attachments(id: string, offset = 0, signal?: AbortSignal, scope?: ConversationScope) {
    return (await http.get<Page<AgentAttachment>>(`${conversationPath(id)}/attachments`, { params: { offset, limit: 50, pending: true, ...scope }, signal })).data;
  },
  async uploadAttachment(id: string, file: File, key: string, scope?: ConversationScope) {
    const body = new FormData(); body.append('file', file);
    return (await http.post<AgentAttachment>(`${conversationPath(id)}/attachments/uploads`, body, { headers: { 'Idempotency-Key': key }, params: scope, timeout: 120_000 })).data;
  },
  async referenceAttachment(id: string, source_type: 'media' | 'asset', source_id: string, key: string, scope?: ConversationScope) {
    return (await http.post<AgentAttachment>(`${conversationPath(id)}/attachments/references`, { source_type, source_id }, { params: scope, headers: { 'Idempotency-Key': key } })).data;
  },
  async removeAttachment(id: string, attachmentId: string, scope?: ConversationScope) {
    await http.delete(`${conversationPath(id)}/attachments/${encodeURIComponent(attachmentId)}`, { params: scope });
  },
  async skills(offset = 0, signal?: AbortSignal) {
    return (await http.get<Page<AgentSkill>>(`${root}/skills`, { params: { offset, limit: 50 }, signal })).data;
  },
  async uploadSkill(file: File) {
    const body = new FormData(); body.append('file', file);
    return (await http.post<AgentSkill>(`${root}/skills/uploads`, body)).data;
  },
  async updateSkill(id: string, body: { row_version: string; name?: string; instructions?: string; enabled?: boolean }) {
    return (await http.patch<AgentSkill>(`${root}/skills/${encodeURIComponent(id)}`, body)).data;
  },
  async deleteSkill(id: string, row_version: string) {
    await http.delete(`${root}/skills/${encodeURIComponent(id)}`, { data: { row_version } });
  },
  async updateModelInputs(id: string, body: { row_version: string; image: boolean; audio: boolean }) {
    return (await http.patch<AgentModel>(`${root}/models/${encodeURIComponent(id)}/inputs`, body)).data;
  },
  async models(signal?: AbortSignal) { return (await http.get<AgentModelPage>(`${root}/models`, { signal })).data; },
  async verifyModel(id: string, row_version: string) {
    return (await http.post<AgentModel>(`${root}/models/${encodeURIComponent(id)}/verify`, { row_version }, { timeout: 120_000 })).data;
  },
  async messages(id: string, offset = 0, signal?: AbortSignal, scope?: ConversationScope) {
    return (await http.get<AgentMessagePage>(`${conversationPath(id)}/messages`, { params: { offset, limit: 50, ...scope }, signal })).data;
  },
  async state(id: string, signal?: AbortSignal, scope?: ConversationScope) {
    return (await http.get<ConversationRuntimeState>(`${conversationPath(id)}/state`, { params: scope, signal })).data;
  },
  async runs(id: string, signal?: AbortSignal) {
    return (await http.get<AgentRunPage>(`${conversationPath(id)}/runs`, { params: { offset: 0, limit: 1 }, signal })).data;
  },
  async run(id: string, signal?: AbortSignal) { return (await http.get<AgentRun>(`${root}/runs/${encodeURIComponent(id)}`, { signal })).data; },
  async send(id: string, body: AgentSendInput, key: string) {
    return (await http.post<AgentSendResult>(`${conversationPath(id)}/messages`, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async stop(id: string, scope?: ConversationScope) { return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/stop`, {}, { params: scope })).data; },
  async continue(id: string, artifact_id: string, artifact_row_version: string, scope?: ConversationScope) {
    return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/continue`, { artifact_id, artifact_row_version }, { params: scope })).data;
  },
  async review(id: string, review: AgentReview, decision: 'approved' | 'rejected', scope?: ConversationScope) {
    return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/reviews/${encodeURIComponent(review.tool_call_id)}`,
      { review_version: review.review_version, review_hash: review.review_hash, decision }, { params: scope })).data;
  },
  async status(signal?: AbortSignal) {
    return (await http.get<AgentAvailability>(`${root}/status`, { signal })).data;
  },
  async conversations(projectId: string, episodeId: string, offset = 0, includeArchived = false, signal?: AbortSignal, scope?: ConversationScope, query = '') {
    return (await http.get<AgentConversationPage>(`${root}/conversations`, {
      params: { project_id: projectId, episode_id: episodeId, offset, limit: 20, include_archived: includeArchived, ...scope, ...(query.trim() ? { query: query.trim() } : {}) }, signal,
    })).data;
  },
  async conversation(id: string, signal?: AbortSignal, scope?: ConversationScope) {
    return (await http.get<AgentConversation>(conversationPath(id), { params: scope, signal })).data;
  },
  async createConversation(body: CreateAgentConversation, idempotencyKey: string) {
    return (await http.post<AgentConversation>(`${root}/conversations`, body, { headers: { 'Idempotency-Key': idempotencyKey } })).data;
  },
  async resolveConversation(body: CreateAgentConversation, signal?: AbortSignal) {
    return (await http.post<AgentConversation>(`${root}/conversations/resolve`, body, { signal })).data;
  },
  async updateConversation(id: string, body: UpdateAgentConversation) {
    return (await http.patch<AgentConversation>(conversationPath(id), body)).data;
  },
};
