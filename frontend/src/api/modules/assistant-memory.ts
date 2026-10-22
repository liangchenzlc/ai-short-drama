import { http } from '../http';
import type { Page } from '../types/generations';
import type { AssistantMemory, AssistantMemoryContext, AssistantMemoryCreate, AssistantMemoryPatch, AssistantMemoryRevision, AssistantMemoryReview, AssistantMemorySummary } from '../types/assistant-memory';

const root = '/assistant/memory';
const path = (id: string) => `${root}/${encodeURIComponent(id)}`;
export const assistantMemoryApi = {
  async list(params: { conversation_id?: string; project_id?: string; query?: string; offset: number; include_forgotten: boolean }, signal?: AbortSignal) {
    return (await http.get<Page<AssistantMemory>>(root, { params: { ...params, limit: 30 }, signal })).data;
  },
  async read(id: string, signal?: AbortSignal) {
    return (await http.get<AssistantMemory>(path(id), { signal })).data;
  },
  async create(body: AssistantMemoryCreate, key: string) {
    return (await http.post<AssistantMemory>(root, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async patch(id: string, body: AssistantMemoryPatch) {
    return (await http.patch<AssistantMemory>(path(id), body)).data;
  },
  async forget(id: string, content_version: string) {
    return (await http.delete<AssistantMemory>(path(id), { data: { content_version } })).data;
  },
  async restore(id: string, content_version: string) {
    return (await http.post<AssistantMemory>(`${path(id)}/restore`, { content_version })).data;
  },
  async revisions(id: string, signal?: AbortSignal) {
    return (await http.get<{ items: AssistantMemoryRevision[] }>(`${path(id)}/revisions`, { signal })).data;
  },
  async context(id: string, signal?: AbortSignal) {
    return (await http.get<AssistantMemoryContext>(`${root}/conversations/${encodeURIComponent(id)}/context`, { signal })).data;
  },
  async summary(conversationId: string, summaryId: string, signal?: AbortSignal) {
    return (await http.get<AssistantMemorySummary>(`${root}/conversations/${encodeURIComponent(conversationId)}/summaries/${encodeURIComponent(summaryId)}`, { signal })).data;
  },
  async reviews(id: string, offset: number, signal?: AbortSignal) {
    return (await http.get<Page<AssistantMemoryReview>>(`${root}/conversations/${encodeURIComponent(id)}/reviews`, { params: { offset, limit: 20 }, signal })).data;
  },
  async clarify(jobId: string, index: number, body: { content_version: string; choice: 'keep_current' | 'use_candidate' }, key: string) {
    return (await http.post<AssistantMemoryReview>(`${root}/jobs/${encodeURIComponent(jobId)}/clarifications/${index}`, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async retryJob(jobId: string, message_version: string, key: string) {
    return (await http.post<AssistantMemoryContext['jobs'][number]>(`${root}/jobs/${encodeURIComponent(jobId)}/retry`, { message_version }, { headers: { 'Idempotency-Key': key } })).data;
  },
};
