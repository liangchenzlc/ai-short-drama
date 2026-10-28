import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage, AgentRun } from '@host/types/assistant/engine';
import type { HistoryRequest } from '@host/types/assistant/history/AssistantDiscussionHistory';
export interface AssistantTranscriptProps {
  pinned: import('react').MutableRefObject<boolean>;
  runtime: {
    run: AgentRun | null;
    messages: AgentMessage[];
    operations: AssistantOperation[];
    queuedRuns: AgentRun[];
    connection: string;
    error: string;
    accessEnded: boolean;
    total: number;
    hasMoreTasks: boolean;
    olderLoading: boolean;
    loading: boolean;
    delta: string;
    older: () => Promise<unknown>;
    olderTasks: () => Promise<unknown>;
    reconnect: () => void;
  };
  layout?: 'standard';
  active: boolean;
  stopping: boolean;
  uncertain: boolean;
  contextDisabled: boolean;
  readOnly: boolean;
  busy: boolean;
  setDraft: (text: string) => void;
  setUndoRequest: (id: string) => void;
  setHistoryRequest: (request: HistoryRequest) => void;
  stop: (id: string) => Promise<unknown> | undefined;
}
