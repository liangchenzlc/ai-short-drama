import { http } from '@host/api/http';
import type {
  AssistantAttachmentMetadata,
  AssistantControlAccepted,
  AssistantControlInput,
  AssistantDeletionPreview,
  AssistantGenerationPolicy,
  AssistantMemorySelection,
  AssistantOperation,
  AssistantSubjectKind,
  AssistantSubjectMetadata,
  AssistantSubjectOption,
  AssistantSubjectReference,
  AssistantTaskAccepted,
  AssistantTaskSendInput,
  AssistantTaskSnapshot,
  AssistantUndoInput,
  AssistantUndoPreview,
  AssistantUndoReceipt,
  DiscussionEligibility,
  DiscussionRevision,
  DiscussionRevisionInput,
  ProjectAssistantConversation,
} from '@host/types/assistant/api';
import type { AgentAttachment, AgentMessagePage } from '@host/types/assistant/engine';
import type { Page } from '@host/types/generations/api';
const root = '/assistant';
const conversationPath = (id: string) => `${root}/conversations/${encodeURIComponent(id)}`;
const globalPath = (id: string) => `${root}/global/conversations/${encodeURIComponent(id)}`;

export const assistantApi = {
  async undoPreview(id: string, signal?: AbortSignal) {
    return (
      await http.get<AssistantUndoPreview>(`${root}/operations/${encodeURIComponent(id)}/undo-preview`, { signal })
    ).data;
  },
  async undoBatch(id: string, body: AssistantUndoInput, key: string) {
    return (
      await http.post<AssistantUndoReceipt>(`${root}/operations/${encodeURIComponent(id)}/undo`, body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async discussionEligibility(id: string, messageId: string, signal?: AbortSignal) {
    return (
      await http.get<DiscussionEligibility>(
        `${globalPath(id)}/messages/${encodeURIComponent(messageId)}/revision-eligibility`,
        { signal },
      )
    ).data;
  },
  async reviseDiscussion(id: string, messageId: string, body: DiscussionRevisionInput, key: string) {
    return (
      await http.post<AssistantTaskAccepted>(
        `${globalPath(id)}/messages/${encodeURIComponent(messageId)}/revisions`,
        body,
        { headers: { 'Idempotency-Key': key } },
      )
    ).data;
  },
  async discussionRevisions(id: string, messageId: string, offset = 0, signal?: AbortSignal) {
    return (
      await http.get<Page<DiscussionRevision>>(
        `${globalPath(id)}/messages/${encodeURIComponent(messageId)}/revisions`,
        { params: { offset, limit: 30 }, signal },
      )
    ).data;
  },
  async generationPolicy(signal?: AbortSignal) {
    return (
      await http.get<AssistantGenerationPolicy>(`${root}/generation-policy`, {
        signal,
      })
    ).data;
  },
  async updateGenerationPolicy(media_generation_enabled: boolean, policy_version: string) {
    return (
      await http.patch<AssistantGenerationPolicy>(`${root}/generation-policy`, {
        media_generation_enabled,
        policy_version,
      })
    ).data;
  },
  async globalConversations(projectId: string, offset = 0, signal?: AbortSignal, query = '') {
    return (
      await http.get<Page<ProjectAssistantConversation>>(`${root}/global/conversations`, {
        params: {
          project_id: projectId,
          offset,
          limit: 20,
          query: query.trim() || undefined,
        },
        signal,
      })
    ).data;
  },
  async createGlobalConversation(projectId: string, key: string) {
    return (
      await http.post<ProjectAssistantConversation>(
        `${root}/global/conversations`,
        { project_id: projectId },
        { headers: { 'Idempotency-Key': key } },
      )
    ).data;
  },
  async globalConversation(id: string, signal?: AbortSignal) {
    return (await http.get<ProjectAssistantConversation>(globalPath(id), { signal })).data;
  },
  async renameGlobalConversation(id: string, row_version: string, title: string) {
    return (
      await http.patch<ProjectAssistantConversation>(globalPath(id), {
        row_version,
        title,
      })
    ).data;
  },
  async deletionMemory(id: string, offset = 0, signal?: AbortSignal) {
    return (
      await http.get<AssistantDeletionPreview>(`${globalPath(id)}/deletion-memory`, {
        params: { offset, limit: 30 },
        signal,
      })
    ).data;
  },
  async deleteGlobalConversation(
    id: string,
    row_version: string,
    clear_derived_memory: boolean,
    key: string,
    memory_selections?: AssistantMemorySelection[],
  ) {
    return (
      await http.delete<ProjectAssistantConversation>(globalPath(id), {
        data: {
          row_version,
          clear_derived_memory,
          ...(memory_selections ? { memory_selections } : {}),
        },
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async taskState(id: string, signal?: AbortSignal, beforeTaskId?: string) {
    return (
      await http.get<AssistantTaskSnapshot>(`${globalPath(id)}/state`, {
        params: { before_task_id: beforeTaskId, limit: 20 },
        signal,
      })
    ).data;
  },
  async taskMessages(id: string, offset = 0, signal?: AbortSignal) {
    return (
      await http.get<AgentMessagePage>(`${globalPath(id)}/messages`, {
        params: { offset, limit: 50 },
        signal,
      })
    ).data;
  },
  async sendTask(id: string, body: AssistantTaskSendInput, key: string) {
    return (
      await http.post<AssistantTaskAccepted>(`${globalPath(id)}/messages`, body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async controlTask(id: string, body: AssistantControlInput, key: string) {
    return (
      await http.post<AssistantControlAccepted>(`${root}/tasks/${encodeURIComponent(id)}/control`, body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async subjects(taskId: string, signal?: AbortSignal) {
    return (
      await http.get<AssistantSubjectMetadata>(`${root}/tasks/${encodeURIComponent(taskId)}/subjects`, { signal })
    ).data;
  },
  async taskAttachments(taskId: string, signal?: AbortSignal) {
    return (
      await http.get<AssistantAttachmentMetadata>(`${root}/tasks/${encodeURIComponent(taskId)}/attachments`, { signal })
    ).data;
  },
  async subjectDirectory(projectId: string, kind: AssistantSubjectKind, query = '', offset = 0, signal?: AbortSignal) {
    return (
      await http.get<Page<AssistantSubjectOption>>(`${root}/subjects`, {
        params: { project_id: projectId, kind, query, offset, limit: 20 },
        signal,
      })
    ).data;
  },
  async inspectSubjects(subjects: AssistantSubjectReference[], signal?: AbortSignal) {
    return (await http.post<AssistantSubjectReference[]>(`${root}/subjects/metadata`, { subjects }, { signal })).data;
  },
  async operations(taskId: string, afterId?: string, signal?: AbortSignal) {
    return (
      await http.get<{
        items: AssistantOperation[];
        has_more: boolean;
        next_after_id: string | null;
        snapshot_cursor: number;
      }>(`${root}/tasks/${encodeURIComponent(taskId)}/operations`, {
        params: { after_id: afterId, limit: 50 },
        signal,
      })
    ).data;
  },
  async operation(id: string, signal?: AbortSignal) {
    return (
      await http.get<AssistantOperation & { result: Record<string, unknown> | null }>(
        `${root}/operations/${encodeURIComponent(id)}`,
        { signal },
      )
    ).data;
  },
  async attachments(id: string, offset = 0, signal?: AbortSignal) {
    return (
      await http.get<Page<AgentAttachment>>(`${conversationPath(id)}/attachments`, {
        params: { offset, limit: 50, pending: true },
        signal,
      })
    ).data;
  },
  async uploadAttachment(id: string, file: File, key: string) {
    const body = new FormData();
    body.append('file', file);
    return (
      await http.post<AgentAttachment>(`${conversationPath(id)}/attachments/uploads`, body, {
        headers: { 'Idempotency-Key': key },
        timeout: 120_000,
      })
    ).data;
  },
  async referenceAttachment(id: string, source_type: 'media' | 'asset', source_id: string, key: string) {
    return (
      await http.post<AgentAttachment>(
        `${conversationPath(id)}/attachments/references`,
        { source_type, source_id },
        { headers: { 'Idempotency-Key': key } },
      )
    ).data;
  },
  async removeAttachment(id: string, attachmentId: string) {
    await http.delete(`${conversationPath(id)}/attachments/${encodeURIComponent(attachmentId)}`);
  },
};
