import { memo } from 'react';
import { Button } from 'antd';
import type { AgentMessage } from '../../api/types/agents';
import { PreviewImage } from '../../components/ui/ImagePreview';

export const AgentMessageBubble = memo(function AgentMessageBubble({ message, onOpenArtifact }: {
  message: AgentMessage; onOpenArtifact: (id: string) => void;
}) {
  return <article className={`agent-message is-${message.role}`} data-message-id={message.id}>
    <div className="agent-message-author">{message.role === 'user' ? '你' : 'Agent'}</div>
    <p>{message.content}</p>
    {message.references?.length > 0 && <div className="agent-message-references">{message.references.map((reference, index) => {
      const name = typeof reference.name === 'string' ? reference.name : '引用资料';
      const url = typeof reference.url === 'string' ? reference.url : null;
      return <div key={`${String(reference.id ?? index)}:${index}`}>
        {reference.kind === 'image' && url ? <PreviewImage src={url} alt={name}/>
          : reference.kind === 'video' && url ? <video src={url} controls preload="metadata" aria-label={`${name}预览`}/>
            : reference.kind === 'audio' && url ? <audio src={url} controls preload="metadata" aria-label={`${name}试听`}/> : null}
        <span>{reference.type === 'skill' ? `Skill：${name}` : name}</span>
        {reference.kind === 'text' && typeof reference.text_preview === 'string' && <details><summary>查看文本资料</summary><p>{reference.text_preview}</p></details>}
      </div>;
    })}</div>}
    {message.artifacts.length > 0 && <div className="agent-message-results"><span className="agent-message-result-note">生成结果已保留为候选，请核对后采用。</span>{message.artifacts.filter(item => typeof item.artifact_id === 'string' && /^\d+$/.test(item.artifact_id)).map(item => <Button key={String(item.artifact_id)} size="small" onClick={() => onOpenArtifact(String(item.artifact_id))}>核对候选</Button>)}</div>}
  </article>;
});
