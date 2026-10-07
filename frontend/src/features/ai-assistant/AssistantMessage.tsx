import { memo, useState } from 'react';
import { Button } from 'antd';
import { Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AgentMessage } from '../../api/types/agents';
import { PreviewImage } from '../../components/ui/ImagePreview';

/** 沿用画布助手的安全 Markdown 默认值，不开启原始 HTML。 */
export function AssistantReply({ text }: { text: string }) {
  return <div className="ai-assistant-reply"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}

export const AssistantMessage = memo(function AssistantMessage({ message }: { message: AgentMessage }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState('');
  const user = message.role === 'user';
  async function copy() {
    try { await navigator.clipboard.writeText(message.content); setCopied(true); setCopyError(''); }
    catch { setCopyError('复制失败，请选中回复文字复制。'); }
  }
  return <article className={user ? 'ai-assistant-user' : 'ai-assistant-turn'} data-message-id={message.id}>
    {user ? <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span> : <AssistantReply text={message.content}/>}
    {message.references?.length > 0 ? <div className="ai-assistant-references" aria-label="消息引用资料">{message.references.map((reference, index) => {
      const name = typeof reference.name === 'string' ? reference.name : reference.type === 'context' ? '作品上下文' : '引用资料';
      const url = typeof reference.url === 'string' ? reference.url : null;
      return <div key={`${String(reference.id ?? index)}:${index}`}>
        {reference.kind === 'image' && url ? <PreviewImage src={url} alt={name}/>
          : reference.kind === 'video' && url ? <video src={url} controls preload="metadata" aria-label={`${name}预览`}/>
            : reference.kind === 'audio' && url ? <audio src={url} controls preload="metadata" aria-label={`${name}试听`}/> : null}
        <span>{reference.type === 'skill' ? `Skill：${name}` : name}</span>
        {reference.type === 'source' && (reference.truncated === true || Array.isArray(reference.truncated) && reference.truncated.length > 0) ? <span>作品上下文已截取</span> : null}
        {reference.kind === 'text' && typeof reference.text_preview === 'string' ? <details><summary>查看文本资料</summary><p>{reference.text_preview}</p></details> : null}
      </div>;
    })}</div> : null}
    {!user ? <div className="ai-assistant-reply-actions"><Button type="text" size="small" aria-label="复制回复" icon={copied ? <Check size={13}/> : <Copy size={13}/>} onClick={() => void copy()}>{copied ? '已复制' : '复制'}</Button>{copyError ? <span role="status">{copyError}</span> : null}</div> : null}
  </article>;
});
