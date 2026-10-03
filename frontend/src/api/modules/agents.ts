import { http } from '../http';
import type { AgentAvailability, AgentConversation, AgentConversationPage, CreateAgentConversation, UpdateAgentConversation,
  AgentModel, AgentModelPage, AgentMessagePage, AgentRun, AgentRunPage, AgentSendInput, AgentSendResult, AgentReview } from '../types/agents';

const root = '/agent';
const conversationPath = (id: string) => `${root}/conversations/${encodeURIComponent(id)}`;

export const agentsApi = {
  async models(signal?: AbortSignal) { return (await http.get<AgentModelPage>(`${root}/models`, { signal })).data; },
  async verifyModel(id: string, row_version: number) {
    return (await http.post<AgentModel>(`${root}/models/${encodeURIComponent(id)}/verify`, { row_version }, { timeout: 120_000 })).data;
  },
  async messages(id: string, offset = 0, signal?: AbortSignal) {
    return (await http.get<AgentMessagePage>(`${conversationPath(id)}/messages`, { params: { offset, limit: 50 }, signal })).data;
  },
  async runs(id: string, signal?: AbortSignal) {
    return (await http.get<AgentRunPage>(`${conversationPath(id)}/runs`, { params: { offset: 0, limit: 1 }, signal })).data;
  },
  async run(id: string, signal?: AbortSignal) { return (await http.get<AgentRun>(`${root}/runs/${encodeURIComponent(id)}`, { signal })).data; },
  async send(id: string, body: AgentSendInput, key: string) {
    return (await http.post<AgentSendResult>(`${conversationPath(id)}/messages`, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async stop(id: string) { return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/stop`, {})).data; },
  async continue(id: string, artifact_id: string, artifact_row_version: number) {
    return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/continue`, { artifact_id, artifact_row_version })).data;
  },
  async review(id: string, review: AgentReview, decision: 'approved' | 'rejected') {
    return (await http.post<AgentRun>(`${root}/runs/${encodeURIComponent(id)}/reviews/${encodeURIComponent(review.tool_call_id)}`,
      { review_version: review.review_version, review_hash: review.review_hash, decision })).data;
  },
  async status(signal?: AbortSignal) {
    return (await http.get<AgentAvailability>(`${root}/status`, { signal })).data;
  },
  async conversations(projectId: string, episodeId: string, offset = 0, includeArchived = false, signal?: AbortSignal) {
    return (await http.get<AgentConversationPage>(`${root}/conversations`, {
      params: { project_id: projectId, episode_id: episodeId, offset, limit: 20, include_archived: includeArchived }, signal,
    })).data;
  },
  async conversation(id: string, signal?: AbortSignal) {
    return (await http.get<AgentConversation>(conversationPath(id), { signal })).data;
  },
  async createConversation(body: CreateAgentConversation, idempotencyKey: string) {
    return (await http.post<AgentConversation>(`${root}/conversations`, body, { headers: { 'Idempotency-Key': idempotencyKey } })).data;
  },
  async updateConversation(id: string, body: UpdateAgentConversation) {
    return (await http.patch<AgentConversation>(conversationPath(id), body)).data;
  },
};
