import { ReasoningBlock } from '@host/features/assistant/messages/ReasoningBlock';
import { traceChildren, traceStatus, traceTitle } from '@host/features/assistant/messages/trace-presentation';
import type { TraceBlockProps } from '@host/types/assistant/messages/trace';
import type { AssistantTraceBlock as Block } from '@host/types/assistant/trace';
import { ToolTraceVisibility as Visibility } from '@host/types/assistant/trace';
import { BookOpen, Clock3, GitBranch, Terminal, Wrench } from 'lucide-react';
import { useEffect, useState } from 'react';

function messageError(block?: Block, operation?: TraceBlockProps['operation']) {
  const error = block?.error ?? operation?.error;
  return error && typeof error.message === 'string' ? error.message : '';
}

function titleFor(block?: Block, operation?: TraceBlockProps['operation']) {
  if (block) return traceTitle(block);
  const action = operation?.action || operation?.label || '执行创作操作';
  const target = operation?.target;
  return target?.kind === 'command' ? action : target ? `${action}《${target.display_name}》` : action;
}

function ActivityIcon({ kind }: { kind: string }) {
  const common = { size: 16, 'aria-hidden': true as const };
  if (kind === 'skill') return <BookOpen {...common} />;
  if (kind === 'sandbox') return <Terminal {...common} />;
  if (kind === 'subtask') return <GitBranch {...common} />;
  if (kind === 'wait') return <Clock3 {...common} />;
  return <Wrench {...common} />;
}

function Activity(props: TraceBlockProps & { blocks?: readonly Block[] }) {
  const block = props.block;
  const operation = props.operation;
  const status = block?.status ?? operation?.outcome ?? 'running';
  const id = block?.block_id ?? operation?.id ?? '';
  const kind = block?.kind ?? 'tool';
  const expansionId = `sandbox:${block?.run_id ?? operation?.run_id}:${id}`;
  const isRunning = status === 'running';
  const [shown, setShown] = useState(!isRunning);
  useEffect(() => {
    if (!isRunning) {
      setShown(true);
      return;
    }
    setShown(false);
    const timer = setTimeout(() => setShown(true), 200);
    return () => clearTimeout(timer);
  }, [id, isRunning]);

  if (!shown || (block && block.kind !== 'reasoning' && block.trace_visibility === Visibility.HIDDEN))
    return null;
  const title = titleFor(block, operation);
  const summary = block?.summary || operation?.summary || '';
  const error = messageError(block, operation);
  const children = block?.kind === 'subtask' ? traceChildren(block, props.blocks ?? []) : [];
  const logs = block?.kind === 'sandbox'
    ? ['stdout', 'stderr'].flatMap((name) => {
        const channel = block.channels[name];
        return channel?.preview ? [{ name, channel }] : [];
      })
    : [];
  const row = (
    <span className="assistant-trace-row">
      <ActivityIcon kind={kind} />
      <span className="assistant-trace-title">{title}</span>
      <span className="assistant-trace-status" data-status={status} aria-live="polite">
        {(block?.tool_name ?? operation?.tool_name) === 'workbench__submit_generation_batch' && status === 'succeeded'
          ? '已提交'
          : traceStatus(status)}
      </span>
      {summary ? <span className="assistant-trace-summary">{summary}</span> : null}
      {error ? <span className="assistant-trace-error" role="alert">{error}</span> : null}
    </span>
  );
  const childRows = children.length ? (
    <div className="assistant-subtask-children" aria-label="子任务轨迹">
      {children.map((child) => (
        <AssistantTraceBlock
          key={child.block_id}
          block={child}
          blocks={props.blocks ?? []}
          conversationId={props.conversationId}
          expandedItems={props.expandedItems}
          onExpandedChange={props.onExpandedChange}
        />
      ))}
    </div>
  ) : null;
  return logs.length ? (
    <details className="assistant-trace-block assistant-sandbox-activity" data-status={status}
      open={props.expandedItems.has(expansionId)}
      onToggle={event => props.onExpandedChange([expansionId], event.currentTarget.open)}>
      <summary>{row}</summary>
      <div className="assistant-sandbox-details">
        {logs.map(({ name, channel }) => (
          <pre className="assistant-trace-log" key={name} tabIndex={0} aria-label={name === 'stderr' ? '错误输出' : '执行输出'}>
            {channel.preview}{channel.truncated ? '\n内容较长，已截取' : ''}
          </pre>
        ))}
        {childRows}
      </div>
    </details>
  ) : <div className="assistant-trace-block" data-status={status}>{row}{childRows}</div>;
}

export function AssistantTraceBlock(props: TraceBlockProps & { blocks?: readonly Block[] }) {
  if (props.block?.kind === 'reasoning') return <ReasoningBlock block={props.block} />;
  return <Activity {...props} />;
}
