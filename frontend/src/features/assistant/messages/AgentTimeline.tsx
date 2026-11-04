import { Bubble } from '@ant-design/x';
import { AssistantMessage, AssistantReply } from '@host/features/assistant/messages/AssistantMessage';
import { AssistantLoading } from '@host/features/assistant/messages/AssistantLoading';
import { AssistantTraceBlock } from '@host/features/assistant/messages/AssistantTraceBlock';
import { buildTimeline, isFailedOperation } from '@host/features/assistant/messages/agent-timeline';
import { OperationResults } from '@host/features/assistant/operations/AssistantOperationResults';
import type { BubbleItems, TimelineProps } from '@host/types/assistant/messages/AgentTimeline';
import type { AssistantTurnPart, TimelineEntry } from '@host/types/assistant/messages/agent-timeline';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';

const RUNNING = new Set(['queued', 'running', 'waiting_generation', 'waiting_input']);
const ACTIVE_ACTIVITY = new Set([
  'running',
  'waiting_media',
  'waiting_input',
  'waiting_generation',
  'reconciling',
  'settling_result',
]);

function partStatus(part: AssistantTurnPart) {
  return part.kind === 'activity' ? part.block?.status ?? part.operation?.outcome ?? '' : '';
}

function partFailed(part: AssistantTurnPart) {
  return part.kind === 'activity' &&
    (part.block?.status === 'failed' || part.block?.status === 'conflict' ||
      part.operation?.outcome === 'failed' || part.operation?.outcome === 'conflict');
}

function renderPart(
  part: AssistantTurnPart,
  props: TimelineProps,
  blocks: NonNullable<TimelineProps['traceBlocks']>,
) {
  if (part.kind === 'text') {
    if (!part.message) return <div className="ai-assistant-streaming"><AssistantReply text={part.text} /></div>;
    return (
      <AssistantMessage
        message={part.message}
        layout={props.layout}
        historyDisabled={props.historyDisabled}
        onHistory={props.onHistory ? (mode) => props.onHistory?.(part.message!, mode) : undefined}
      />
    );
  }
  return (
    <AssistantTraceBlock
      key={part.key}
      block={part.block}
      operation={part.operation}
      blocks={blocks}
      conversationId={props.conversationId ?? ''}
      expandedItems={props.expandedItems}
      onExpandedChange={props.onExpandedChange}
    />
  );
}

function AssistantAgentTurn({ entry, props }: { entry: Extract<TimelineEntry, { kind: 'assistant_turn' }>; props: TimelineProps }) {
  const blocks = props.traceBlocks ?? [];
  const activities = entry.parts.filter((part) => part.kind === 'activity' && part.block?.kind !== 'reasoning');
  const lastActivityIndex = entry.parts.reduce(
    (last, part, index) => part.kind === 'activity' && part.block?.kind !== 'reasoning' ? index : last,
    -1,
  );
  const processParts = lastActivityIndex >= 0 ? entry.parts.slice(0, lastActivityIndex + 1) : [];
  const replyParts = lastActivityIndex >= 0 ? entry.parts.slice(lastActivityIndex + 1) : entry.parts;
  const running = RUNNING.has(entry.status ?? '') || activities.some((part) => ACTIVE_ACTIVITY.has(partStatus(part)));
  // 更早用户尚未载入时段 key 仍会变化；展开选择跟随持久活动，不跟随消息页边界。
  const expansionIds = activities.map(part => `process:${entry.run_id}:${part.key}`);
  const expanded = expansionIds.some(id => props.expandedItems.has(id));
  const previousRunning = useRef(running);
  useLayoutEffect(() => {
    if (previousRunning.current !== running) props.onExpandedChange(expansionIds, false);
    previousRunning.current = running;
  }, [running]);
  const failedCount = processParts.filter(partFailed).length;
  const resultProps = {
    layout: props.layout,
    onUndo: props.onUndo,
    undoDisabled: props.undoDisabled,
  };
  const label = running
    ? `正在处理 ${activities.length} 项`
    : failedCount
      ? `已处理 ${activities.length} 项 · ${failedCount} 项未完成`
      : `已处理 ${activities.length} 项`;

  return (
    <article className="ai-assistant-turn ai-assistant-agent-turn" data-run-id={entry.run_id}>
      {processParts.length > 0 ? (
        <section className="assistant-process" data-running={running} aria-label="执行步骤">
          <button
            type="button"
            className="assistant-process-summary"
            aria-expanded={running || expanded}
            disabled={running}
            onClick={() => props.onExpandedChange(expansionIds, !expanded)}
          >
            {running || expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
            <span>{label}</span>
          </button>
          {running || expanded ? (
            <div className="assistant-process-details" role="list">
              {processParts.map((part) => <div key={part.key} role="listitem">{renderPart(part, props, blocks)}</div>)}
            </div>
          ) : null}
        </section>
      ) : null}
      {replyParts.map((part) => <div className="assistant-reply-part" key={part.key}>{renderPart(part, props, blocks)}</div>)}
      {entry.results.length > 0 ? (
        <div className="assistant-turn-results" aria-label="创作成果">
          {entry.results.map((operation) => (
            <OperationResults key={operation.id} operation={operation} {...resultProps} />
          ))}
        </div>
      ) : null}
      {running && entry.parts.length === 0 ? (
        <AssistantLoading label={entry.status === 'queued' ? '等待回复' : '正在处理'} />
      ) : null}
    </article>
  );
}

export function timelineBubbles(props: TimelineProps): BubbleItems {
  const blocks = props.traceBlocks ?? [];
  return buildTimeline(
    props.messages,
    props.operations,
    blocks,
    props.runs,
    props.streamingParts,
    props.streamingRunId,
  ).map((entry) => {
    if (entry.kind === 'assistant_turn') {
      return {
        key: entry.key,
        role: 'assistant',
        placement: 'start' as const,
        variant: 'borderless' as const,
        className: 'ai-assistant-bubble-reply',
        content: <AssistantAgentTurn entry={entry} props={props} />,
      };
    }
    const user = entry.message.role === 'user';
    return {
      key: entry.key,
      role: entry.message.role,
      placement: user ? ('end' as const) : ('start' as const),
      variant: 'borderless' as const,
      className: user ? 'ai-assistant-bubble-user' : 'ai-assistant-bubble-reply',
      content: (
        <AssistantMessage
          message={entry.message}
          layout={props.layout}
          historyDisabled={props.historyDisabled}
          onHistory={!user && props.onHistory ? (mode) => props.onHistory?.(entry.message, mode) : undefined}
        />
      ),
    };
  }) as BubbleItems;
}
