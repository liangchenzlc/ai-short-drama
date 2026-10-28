import { Bubble, Prompts, Welcome } from '@ant-design/x';
import { timelineBubbles } from '@host/features/assistant/messages/AgentTimeline';
import { AssistantLoading } from '@host/features/assistant/messages/AssistantLoading';
import { AssistantReply } from '@host/features/assistant/messages/AssistantMessage';
import type { AssistantTranscriptProps } from '@host/types/assistant/messages/transcript';
import { Button, Skeleton } from 'antd';
import { ArrowUpRight, Clapperboard } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
export function AssistantTranscript({
  pinned,
  runtime,
  layout,
  active,
  stopping,
  uncertain,
  contextDisabled,
  readOnly,
  busy,
  setDraft,
  setUndoRequest,
  setHistoryRequest,
  stop,
}: AssistantTranscriptProps) {
  const [queuedStalled, setQueuedStalled] = useState(false);
  const queuedRunId = runtime.run?.status === 'queued' ? runtime.run.id : null;
  useEffect(() => {
    if (!queuedRunId) {
      setQueuedStalled(false);
      return;
    }
    const timer = setTimeout(() => setQueuedStalled(true), 20_000);
    return () => {
      clearTimeout(timer);
      setQueuedStalled(false);
    };
  }, [queuedRunId]);
  const [newContent, setNewContent] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const node = transcript.current;
    if (!node) return;
    if (prepend.current) {
      node.scrollTop = prepend.current.top + node.scrollHeight - prepend.current.height;
      prepend.current = null;
    } else if (pinned.current) node.scrollTop = node.scrollHeight;
    else setNewContent(true);
  }, [runtime.messages, runtime.delta, runtime.run?.status]);
  const queuedRuns = runtime.queuedRuns.filter((run) => run.id !== runtime.run?.id);
  // 会话连接仍监听其他标签页，但终态任务不再显示持续加载，避免误示成果尚未完成。
  const reconnecting =
    (active || queuedRuns.length > 0) &&
    runtime.connection === 'reconnecting' &&
    !uncertain &&
    !runtime.error;
  return (
    <>
      <div
        ref={transcript}
        className="ai-assistant-log"
        aria-label="助手消息"
        tabIndex={0}
        onScroll={(event) => {
          const node = event.currentTarget;
          pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
          if (pinned.current) setNewContent(false);
        }}
      >
        {runtime.messages.length < runtime.total || runtime.hasMoreTasks ? (
          <Button
            size="small"
            loading={runtime.olderLoading}
            onClick={() => {
              const node = transcript.current;
              if (node)
                prepend.current = {
                  height: node.scrollHeight,
                  top: node.scrollTop,
                };
              if (runtime.messages.length < runtime.total) void runtime.older();
              else void runtime.olderTasks();
            }}
          >
            载入更早
          </Button>
        ) : null}
        {runtime.loading ? (
          <Skeleton active title={false} paragraph={{ rows: 5 }} />
        ) : !runtime.messages.length && !active ? (
          <div className="ai-assistant-empty">
            <Welcome
              variant="borderless"
              icon={<Clapperboard size={26} aria-hidden="true" />}
              title={runtime.accessEnded ? '对话暂不可访问' : '把想法聊清楚'}
              description={runtime.accessEnded ? '恢复登录和项目访问后可重新读取。' : '一起梳理故事、画面和创作方向。'}
            />
            {!runtime.accessEnded ? (
              <Prompts
                vertical
                items={['帮我梳理故事的节奏与冲突', '一起完善角色的动机与关系', '为当前作品提供创作建议'].map(
                  (text) => ({
                    key: text,
                    label: (
                      <button
                        className="ai-assistant-starter"
                        type="button"
                        disabled={readOnly}
                        onClick={() => setDraft(text)}
                      >
                        <span>{text}</span>
                        <ArrowUpRight size={15} />
                      </button>
                    ),
                    disabled: readOnly,
                  }),
                )}
              />
            ) : null}
          </div>
        ) : null}
        {!runtime.loading && (runtime.messages.length > 0 || active) ? (
          <Bubble.List
            className="ai-assistant-bubbles"
            autoScroll={false}
            items={[
              ...timelineBubbles({
                messages: runtime.messages,
                operations: runtime.operations,
                layout,
                onUndo: setUndoRequest,
                undoDisabled: contextDisabled || runtime.accessEnded,
                historyDisabled: contextDisabled || runtime.loading || runtime.accessEnded,
                onHistory:
                  layout === 'standard'
                    ? (message, mode) => {
                        if (message.task_id) setHistoryRequest({ message, mode });
                      }
                    : undefined,
              }),
              ...(active && runtime.run && (runtime.delta || (!reconnecting && !stopping))
                ? [
                    {
                      key: `stream:${runtime.run.id}`,
                      role: 'assistant',
                      placement: 'start' as const,
                      variant: 'borderless' as const,
                      className: 'ai-assistant-bubble-reply',
                      content: (
                        <article className="ai-assistant-turn ai-assistant-streaming" data-run-id={runtime.run.id}>
                          {runtime.delta ? (
                            <AssistantReply text={runtime.delta} />
                          ) : (
                            <AssistantLoading label={runtime.run.status === 'queued' ? '等待回复' : '正在回复'} />
                          )}
                        </article>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        ) : null}
        {queuedStalled && runtime.connection === 'live' && (
          <div className="ai-assistant-connection ai-assistant-stalled" role="status">
            <span>消息已提交，但迟迟未被调度。请确认后端调度进程正在运行。</span>
            <Button size="small" onClick={() => runtime.reconnect()}>
              重新连接
            </Button>
          </div>
        )}
        {runtime.run?.status === 'failed' ? (
          <p className="ai-assistant-failed" role="status">
            {runtime.run.error?.message || '本次任务尚未完成，已有成果和消息已保留。请根据上方提示处理后继续。'}
          </p>
        ) : null}
        {stopping && <AssistantLoading label="当前工具完整结束后停止" />}
        {reconnecting && (
          <div className="ai-assistant-connection">
            <AssistantLoading label="正在恢复连接" />
            <Button type="text" size="small" onClick={() => runtime.reconnect()}>
              重新连接
            </Button>
          </div>
        )}
        {queuedRuns.length > 0 && (
          <div className="ai-assistant-queued" aria-label="排队消息">
            {queuedRuns.map((run) => (
              <div key={run.id}>
                <span>排队位置 {run.queue_position || '等待中'}</span>
                <Button type="text" size="small" disabled={readOnly || !!busy} onClick={() => void stop(run.id)}>
                  取消排队
                </Button>
              </div>
            ))}
          </div>
        )}
        {runtime.run?.status === 'cancelled' ? (
          <p className="ai-assistant-meta">已中断，已生成的内容已保留；可以补充要求继续。</p>
        ) : null}
      </div>
      {newContent ? (
        <Button
          className="ai-assistant-new-content"
          size="small"
          onClick={() => {
            if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
            pinned.current = true;
            setNewContent(false);
          }}
        >
          查看最新消息
        </Button>
      ) : null}
    </>
  );
}
