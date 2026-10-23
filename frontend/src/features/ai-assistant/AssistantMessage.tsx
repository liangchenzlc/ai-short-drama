import { memo } from 'react';
import { Button } from 'antd';
import { FileText, History, Music2, Video } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AgentMessage } from '../../api/types/agents';
import { PreviewImage } from '../../components/ui/ImagePreview';

/** 沿用画布助手的安全 Markdown 默认值，不开启原始 HTML。 */
export function AssistantReply({ text }: { text: string }) {
  return <div className="ai-assistant-reply"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}

export const AssistantMessage = memo(function AssistantMessage({ message, layout, onHistory, historyDisabled }: { message: AgentMessage; layout?: 'standard'; onHistory?: (mode: 'edit' | 'regenerate' | 'history') => void; historyDisabled?: boolean }) {
  const user = message.role === 'user';
  const revised = !!onHistory && /^\d+$/.test(message.row_version ?? '1') && BigInt(message.row_version ?? '1') > 1n;
  const references = message.references?.filter(reference => reference.type !== 'subject_scope') ?? [];
  const attachments = references.filter(reference => reference.type === 'attachment');
  const materials = references.filter(reference => reference.type === 'subject');
  const others = references.filter(reference => reference.type !== 'attachment' && reference.type !== 'subject');
  return <article className={user ? 'ai-assistant-user' : 'ai-assistant-turn'} data-message-id={message.id}>
    {user ? <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span> : <AssistantReply text={message.content}/>}
    {attachments.length || materials.length ? <div className="ai-assistant-message-attachments" aria-label="消息素材">
      {attachments.map((reference, index) => {
        const name = typeof reference.name === 'string' ? reference.name : '附件';
        const url = typeof reference.url === 'string' ? reference.url : null;
        const preview = typeof reference.text_preview === 'string' ? reference.text_preview.trim().slice(0, 80) : '';
        return <figure key={`attachment:${String(reference.id ?? index)}`} title={name}>
          {reference.kind === 'image' && url ? <span className="ai-assistant-message-thumb"><PreviewImage src={url} alt={name} presentation={layout === 'standard' ? 'standard-creation' : undefined}/></span>
            : reference.kind === 'text' ? <span className="ai-assistant-message-text">{preview || name}</span>
              : <span className="ai-assistant-message-icon">{reference.kind === 'video' ? <Video size={16} aria-hidden="true"/> : reference.kind === 'audio' ? <Music2 size={16} aria-hidden="true"/> : <FileText size={16} aria-hidden="true"/>}</span>}
        </figure>;
      })}
      {materials.map((reference, index) => {
        const name = typeof reference.name === 'string' ? reference.name : '素材';
        return <figure key={`subject:${String(reference.id ?? index)}`} title={name}>
          <figcaption className="ai-assistant-message-material"><FileText size={13} aria-hidden="true"/><span>{name}</span></figcaption>
        </figure>;
      })}
    </div> : null}
    {others.length ? <div className="ai-assistant-references" aria-label="消息引用资料">{others.map((reference, index) => {
      const name = typeof reference.name === 'string' ? reference.name : reference.type === 'context' ? '作品上下文' : '引用资料';
      const url = typeof reference.url === 'string' ? reference.url : null;
      return <div key={`${String(reference.id ?? index)}:${index}`}>
        {reference.kind === 'image' && url ? <PreviewImage src={url} alt={name} presentation={layout === 'standard' ? 'standard-creation' : undefined}/>
          : reference.kind === 'video' && url ? <video src={url} controls preload="metadata" aria-label={`${name}预览`}/>
            : reference.kind === 'audio' && url ? <audio src={url} controls preload="metadata" aria-label={`${name}试听`}/> : null}
        <span>{reference.type === 'skill' ? `Skill：${name}` : name}</span>
        {reference.type === 'source' && (reference.truncated === true || Array.isArray(reference.truncated) && reference.truncated.length > 0) ? <span>内容较长，已截取</span> : null}
        {reference.kind === 'text' && typeof reference.text_preview === 'string' ? <details><summary>查看文本资料</summary><p>{reference.text_preview}</p></details> : null}
      </div>;
    })}</div> : null}
    {!user && revised ? <div className="ai-assistant-reply-actions">
      <Button type="text" size="small" disabled={historyDisabled} aria-label="查看讨论修订记录" icon={<History size={13}/>} onClick={() => onHistory?.('history')}>修订记录</Button>
    </div> : null}
  </article>;
});
