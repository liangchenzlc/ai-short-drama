export type MemoryScope = 'user' | 'conversation' | 'project';
export type MemoryKind = 'preference' | 'correction' | 'setting' | 'decision' | 'reference';
export interface AssistantMemory {
  id: string; scope_type: MemoryScope; conversation_id: string | null; project_id: string | null;
  kind: MemoryKind; memory_key: string; title: string; content: string; aliases: string[]; tags: string[];
  pinned: boolean; state: 'active' | 'forgotten' | 'superseded'; origin: 'explicit' | 'extracted';
  content_version: string; sources: { conversation_id: string; message_id: string | null; seq: number; subject_keys: string[] }[];
  created_at: string; updated_at: string;
}
export interface AssistantMemoryCreate {
  scope_type: MemoryScope; conversation_id?: string; project_id?: string; kind: MemoryKind;
  memory_key: string; title: string; content: string; aliases: string[]; tags: string[]; pinned: boolean;
}
export interface AssistantMemoryPatch {
  content_version: string; title?: string; content?: string; aliases?: string[]; tags?: string[]; pinned?: boolean;
}
export interface AssistantMemoryRevision {
  content_version: string; change: string; snapshot: Record<string, unknown>; created_at: string;
}
export interface AssistantMemoryContext {
  conversation_id: string; covers_until_seq: number; summary_generation: string;
  summary: AssistantMemorySummary | null;
  jobs: { id: string; kind: 'extract' | 'summary' | 'task_state' | 'purge'; status: 'pending' | 'running' | 'succeeded' | 'failed' | 'stale' | 'cancelled' | 'abandoned';
    provider_state: string; message_version: string; from_seq: number; to_seq: number; attempts: number;
    error: { code: string } | null; usage: Record<string, unknown>; created_at: string; finished_at: string | null;
    retry_job_id?: string | null;
  }[];
}
export interface AssistantMemorySummary {
  id: string; base_summary_id?: string | null; from_seq: number; to_seq: number; token_estimate: number; created_at: string;
  facts: { kind: 'user_fact' | 'proposal' | 'completed' | 'pending'; text: string; source_message_ids: string[];
    subject_keys: string[]; prior_fact_indexes?: number[]; source_summary_id?: string | null }[];
}
export interface AssistantMemoryReview {
  id: string; conversation_id: string; from_seq: number; to_seq: number;
  clarifications: {
    index: number; memory_id: string; content_version: string; candidate_base_version: string;
    title: string; current_content: string | null; candidate_content: string | null; source_message_ids: string[];
    status: 'pending' | 'resolved' | 'changed' | 'unavailable'; choice: 'keep_current' | 'use_candidate' | null;
  }[];
}
