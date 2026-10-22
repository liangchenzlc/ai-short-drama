import type { AgentConversation, AgentMessagePage, AgentRun, AgentSendInput, AgentSendResult } from './agents';

export interface AssistantGenerationPolicy {
  media_generation_enabled: boolean;
  policy_version: string;
  configured_media: ('image' | 'video' | 'audio')[];
  available_media: ('image' | 'video' | 'audio')[];
}

export interface AssistantConversation extends AgentConversation {
  scope_version: 2;
}

export interface AssistantConversationPage {
  items: AssistantConversation[];
  total: number;
  offset: number;
  limit: number;
}

export interface AssistantMessageContext {
  kind: 'project' | 'canvas';
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

export interface GlobalAssistantConversation extends Omit<AgentConversation, 'project_id' | 'scope_version'> {
  project_id: null;
  scope_version: 3;
  status: 'active' | 'deleting' | 'deleted';
}
export interface TaskExecutionTarget {
  mode: 'standard' | 'infinite_canvas'; project_id: string; canvas_id?: string | null;
}
export interface TaskMediaLimits { images: number | null; videos: number | null; audio: number | null }
export type AssistantSubjectKind = 'asset' | 'shot' | 'node' | 'image_group' | 'image_slot' | 'sound_line';
export interface AssistantSubjectReference {
  project_id: string; source_kind: 'project' | 'canvas'; source_id: string;
  kind: AssistantSubjectKind; id: string; revision: string;
  content_revision?: string | null; checksum?: string | null;
}
export interface AssistantSubjectOption extends AssistantSubjectReference {
  name: string; project_name: string; source_name: string;
}
export interface AssistantSelectedSubject extends AssistantSubjectReference {
  subject_key: string; snapshot_id: string; checksum: string; name: string;
  project_name: string; allowed_fields: string[]; legacy_truncated?: boolean;
  source_location?: AssistantSubjectLocation | null;
}
export interface AssistantSubjectLocation {
  project_id: string; kind: 'asset' | 'shot' | 'node' | 'sound_line'; id: string; canvas_id?: string | null;
  image_group_id?: string | null; image_slot_id?: string | null;
}
export interface AssistantSubjectStatus extends AssistantSelectedSubject {
  availability: 'current' | 'changed' | 'unavailable';
  current_revision?: string | null; current_content_revision?: string | null; current_checksum?: string | null;
}
export interface AssistantSubjectMetadata {
  task_id: string; input_revision: string; applied_input_revision: string; pending: boolean;
  items: AssistantSubjectStatus[];
}
export interface AssistantSubjectSelection {
  expected_input_revision: string; retain_keys: string[]; subjects: AssistantSubjectReference[];
}
export interface AssistantSubjectDraft {
  taskId: string | null; expectedInputRevision: string; retainKeys: string[]; added: AssistantSubjectOption[];
}
export interface AssistantTaskAttachment {
  id: string; kind: 'text' | 'image' | 'video' | 'audio'; name: string;
  checksum_sha256: string; available: boolean;
}
export interface AssistantAttachmentMetadata {
  task_id: string; input_revision: string; applied_input_revision: string;
  pending: boolean; items: AssistantTaskAttachment[];
}
export interface AssistantAttachmentSelection { expected_input_revision: string; retain_ids: string[] }
export interface AssistantAttachmentDraft { taskId: string; expectedInputRevision: string; retainIds: string[] }
export type AssistantTaskStatus = 'queued' | 'running' | 'waiting_media' | 'succeeded' | 'stopped' | 'paused_error' | 'paused_budget';
export type AssistantTaskPhase = 'deciding' | 'tool_running' | 'waiting_media' | 'settling_result' | 'reconciling' | 'at_boundary' | 'terminal';
export interface AssistantSkillActivation {
  activation_id: string; skill_id: string; name: string; content_version: string; package_checksum: string;
  source: 'manual' | 'auto'; purpose: string; active: boolean; plan_revision: string; created_at: string; issue?: string | null;
}
export interface AssistantOperation {
  id: string; task_id: string; run_id: string; call_id: string; tool_name: string;
  tool_version: string; plan_revision: string; state_version: string; phase: AssistantTaskPhase;
  outcome: 'running' | 'waiting_media' | 'reconciling' | 'succeeded' | 'failed' | 'conflict';
  result_refs: Record<string, unknown>[]; error: { code: string; message?: string } | null;
  undo_receipts?: AssistantUndoReceipt[];
  skill_activations?: AssistantSkillActivation[];
  created_at: string; finished_at: string | null;
}
export interface AssistantTask {
  id: string; conversation_id: string; goal: string; status: AssistantTaskStatus; phase: AssistantTaskPhase;
  row_version: string; plan_revision: string; input_revision: string; applied_input_revision: string;
  control_seq: string; applied_control_seq: string;
  control_state: 'none' | 'stop_pending' | 'redirect_pending' | 'delete_pending';
  execution_target: TaskExecutionTarget | null; memory_project_ids: string[];
  source_context: { kind: 'project' | 'canvas'; id: string; project_id: string; revision: string } | null;
  selected_subjects: AssistantSelectedSubject[];
  skills: { id: string; name: string; content_version: string; builtin: boolean }[];
  skill_activations?: AssistantSkillActivation[];
  media_limits: TaskMediaLimits; media_usage: Record<string, unknown>;
  active_operation: AssistantOperation | null; error: { code: string; message?: string } | null;
  created_at: string; updated_at: string;
}
export interface AssistantTaskSendInput extends Omit<AssistantSendInput, 'context'> {
  task_id?: string; new_task?: boolean;
  context?: (AssistantMessageContext & { project_id: string; include_document: false }) | null;
  subject_selection?: AssistantSubjectSelection;
  attachment_selection?: AssistantAttachmentSelection;
  execution_target?: TaskExecutionTarget | null; memory_project_ids?: string[]; media_limits?: TaskMediaLimits;
}
export interface AssistantTaskAccepted extends AgentSendResult { task: AssistantTask }
export interface AssistantMemorySelection { id: string; content_version: string }
export interface AssistantDeletionInput {
  row_version: string; clear_derived_memory: boolean; memory_selections?: AssistantMemorySelection[];
}
export interface AssistantDeletionMemory {
  id: string; content_version: string; scope_type: 'user' | 'project'; project_id: string | null;
  title: string; content_preview: string; protection_reasons: ('user_edited' | 'independent_sources')[];
}
export interface AssistantDeletionPreview {
  conversation_id: string; row_version: string; items: AssistantDeletionMemory[];
  total: number; offset: number; limit: number;
}
export interface AssistantControlInput {
  kind: 'stop' | 'redirect' | 'resume'; expected_control_seq?: string; content?: string;
}
export interface AssistantControlAccepted { control_id: string; task: AssistantTask; run: AgentRun; cursor: number }
export interface AssistantTaskInputState {
  task_id: string; message_id: string; input_revision: string; applied_plan_revision: string | null;
}
export interface AssistantTaskSnapshot {
  conversation_id: string; snapshot_cursor: number; resume_cursor: number;
  messages: AgentMessagePage; runs: AgentRun[]; tasks: AssistantTask[]; operations: AssistantOperation[];
  inputs: AssistantTaskInputState[]; has_more_tasks: boolean; has_more_operations: boolean;
}
export interface DiscussionRevisionInput {
  kind: 'edit' | 'regenerate'; row_version: string; content?: string;
}
export interface DiscussionEligibility {
  allowed: boolean; code: string | null; reason: string | null; row_version: string; current_content: string;
}
export interface DiscussionRevision {
  id: string; kind: 'edit' | 'regenerate'; row_version: string; content: string;
  source_message_id: string; source_version: string; run_id: string; created_at: string;
}
export interface AssistantUndoInput { preview_hash: string; selected_keys: string[] }
export interface AssistantUndoItem { key: string; kind: string; id: string; name: string; allowed: boolean; reason: string | null; requires: string[] }
export interface AssistantUndoReceipt { id: string; operation_id: string; selected_keys: string[]; result_refs: Record<string, unknown>[]; created_at: string }
export interface AssistantUndoPreview { operation_id: string; preview_hash: string; items: AssistantUndoItem[]; receipts: AssistantUndoReceipt[] }
