export interface AssistantExecutionTarget {
  kind: string;
  id: string | null;
  display_name: string;
  version?: string | null;
  path?: string | null;
  offset?: number | null;
  length?: number | null;
}

export interface AssistantExecutionDisplay {
  action: string;
  target: AssistantExecutionTarget;
  title: string;
  summary?: string;
}

export interface AssistantTraceChannel {
  preview: string;
  offset: number;
  total_bytes: number;
  truncated: boolean;
}

export interface AssistantTraceResource {
  resource_id: string;
  name: string;
  kind: string;
  byte_size: number | null;
  total_bytes: number | null;
  truncated: boolean;
  available: boolean;
}

export enum ToolTraceVisibility {
  VISIBLE = 'visible',
  HIDDEN = 'hidden',
}

export interface AssistantTraceBlock extends AssistantExecutionDisplay {
  block_id: string;
  block_version: string;
  run_id: string;
  execution_scope_id: string;
  parent_call_id: string | null;
  tool_call_id?: string | null;
  subtask_id?: string | null;
  model_call_id?: string | null;
  turn_id?: string | null;
  operation_id?: string | null;
  tool_name?: string | null;
  kind: 'reasoning' | 'tool' | 'skill' | 'subtask' | 'sandbox' | 'wait';
  source: string;
  trace_visibility: ToolTraceVisibility;
  status: string;
  started_seq: number;
  updated_seq?: number;
  result_seq?: number | null;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
  duration_ms?: number | null;
  error?: Record<string, unknown> | null;
  channels: Record<string, AssistantTraceChannel>;
  resources: AssistantTraceResource[];
  result_refs: Record<string, unknown>[];
}

export interface AssistantTraceDelta {
  block_id: string;
  block_version: string;
  execution_scope_id: string;
  parent_call_id: string | null;
  tool_call_id?: string | null;
  subtask_id?: string | null;
  source: string;
  channel: string;
  offset: number;
  delta: string;
  created_at: string;
}

export interface AssistantTracePage {
  items: AssistantTraceBlock[];
  has_more: boolean;
  next_before_seq: number | null;
  snapshot_cursor: number;
}

export interface AssistantTraceResourceRead {
  resource_id: string;
  name: string;
  content: string | null;
  offset: number | null;
  length: number | null;
  url: string | null;
}

export interface AssistantTraceProjection {
  traceBlocks: Record<string, AssistantTraceBlock>;
  traceDeltas: Record<string, AssistantTraceDelta[]>;
}
