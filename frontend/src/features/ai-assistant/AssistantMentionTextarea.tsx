import { useEffect, useId, useRef, useState } from 'react';
import { Button, Input } from 'antd';
import { assetLibraries } from '../../api/modules/assets';
import type { AssistantMentionReference } from '../../api/types/assistant';
import { errorMessage } from '../../api/http';

export function AssistantMentionTextarea({ projectId, value, disabled, references = [], onChange, onSubmit, onReference }: {
  projectId: string; value: string; disabled: boolean; onChange: (value: string) => void;
  references?: readonly AssistantMentionReference[];
  onSubmit: () => void; onReference: (reference: AssistantMentionReference) => Promise<boolean>;
}) {
  const [mention, setMention] = useState<{ start: number; end: number; query: string } | null>(null);
  const [assets, setAssets] = useState<AssistantMentionReference[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');
  const [active, setActive] = useState(0);
  const input = useRef<HTMLTextAreaElement | null>(null);
  const composing = useRef(false);
  const current = useRef({ value, mention }); current.current = { value, mention };
  const listId = useId();
  const items = [...references.filter(item => item.name.toLowerCase().includes(mention?.query.toLowerCase() ?? '')), ...assets]
    .filter((item, index, all) => all.findIndex(candidate => candidate.kind === item.kind && candidate.id === item.id) === index);
  useEffect(() => {
    if (!mention) return;
    const controller = new AbortController(); setLoading(true); setError(''); setActive(0); setAssets([]);
    const timer = setTimeout(() => {
      assetLibraries.list({ kind: 'project', projectId }, { q: mention.query, offset: 0, limit: 20 }, controller.signal)
        .then(page => { if (!controller.signal.aborted) setAssets(page.items.map(item => ({ kind: 'asset', id: item.id, name: item.name, revision: item.row_version }))); })
        .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [projectId, mention?.query, !!mention]);
  function change(text: string, caret: number) {
    onChange(text);
    const match = /(?:^|\s)@([^\s@]*)$/.exec(text.slice(0, caret));
    setMention(match ? { start: caret - match[1].length - 1, end: caret, query: match[1] } : null);
  }
  async function select(item: AssistantMentionReference) {
    if (adding || disabled || !mention) return;
    const captured = mention; const original = value; setAdding(true);
    try {
      if (!await onReference(item)) return;
      if (current.current.value === original && current.current.mention === captured) {
        const inserted = `@${item.name} `;
        onChange(original.slice(0, captured.start) + inserted + original.slice(captured.end)); setMention(null);
      }
    } finally { setAdding(false); input.current?.focus(); }
  }
  return <div className="ai-assistant-mention-input">
    {mention ? <div id={listId} className="ai-assistant-mentions" role="listbox" aria-label="引用项目素材">
      {loading ? <span role="status">正在读取素材…</span> : error ? <span role="alert">{error}</span> : null}
      {items.length ? items.map((item, index) =>
        <Button key={`${item.kind}:${item.id}`} type="text" role="option" aria-selected={index === active} disabled={disabled || adding} onMouseDown={event => event.preventDefault()} onClick={() => void select(item)}>{item.name}</Button>) : !loading && !error ? <span>没有匹配的项目素材</span> : null}
      <Button type="text" size="small" onClick={() => setMention(null)}>关闭引用列表</Button>
    </div> : null}
    <Input.TextArea ref={node => { input.current = node?.resizableTextArea?.textArea ?? null; }} autoSize={{ minRows: 3, maxRows: 8 }}
      value={value} maxLength={32000} disabled={disabled || adding} aria-label="给助手的消息"
      aria-controls={mention ? listId : undefined} aria-expanded={!!mention} placeholder="描述你的想法，或用 @ 引用素材"
      onChange={event => change(event.target.value, event.target.selectionStart)}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => {
        if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
        if (mention && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setMention(null); }
        else if (mention && items.length && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
          event.preventDefault(); setActive(previous => (previous + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length);
        } else if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault(); if (mention) { if (!loading && items[active]) void select(items[active]); } else onSubmit();
        }
      }}/>
  </div>;
}
