import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage, AgentRun } from '@host/types/assistant/engine';
import type { AssistantTraceBlock } from '@host/types/assistant/trace';
import type { StreamingTextPart } from '@host/types/assistant/runtime/agent-events';

export type AssistantTurnPart =
  | {
      kind: 'text';
      key: string;
      seq: number | null;
      text: string;
      message?: AgentMessage;
    }
  | {
      kind: 'activity';
      key: string;
      seq: number | null;
      block?: AssistantTraceBlock;
      operation?: AssistantOperation;
    };

export type TimelineEntry =
  | { kind: 'message'; key: string; seq: number | null; message: AgentMessage }
  | {
      kind: 'assistant_turn';
      key: string;
      seq: number | null;
      run_id: string;
      status: AgentRun['status'] | null;
      parts: AssistantTurnPart[];
      results: AssistantOperation[];
    };

export type TimelineStreamingPart = StreamingTextPart & { run_id: string };
