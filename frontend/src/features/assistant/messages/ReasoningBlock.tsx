import type { TraceBlockProps } from '@host/types/assistant/messages/trace';

export function ReasoningBlock(props: TraceBlockProps) {
  const channel = props.block.channels.reasoning;
  if (!channel?.preview) return null;
  return <details className="assistant-trace-block assistant-reasoning-block">
    <summary>思考{channel.truncated ? '（预览已截断）' : ''}</summary>
    <pre className="assistant-trace-log" tabIndex={0}>{channel.preview}</pre>
  </details>;
}
