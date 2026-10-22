import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Sender, Suggestion } from '@ant-design/x';
import { Button, Input, Popover, type GetProps, type GetRef } from 'antd';
import { ArrowUp, Square } from 'lucide-react';
import { assetLibraries } from '../../api/modules/assets';
import { agentsApi } from '../../api/modules/agents';
import type { AgentSkill } from '../../api/types/agents';
import type { AssistantMentionReference } from '../../api/types/assistant';
import { errorMessage } from '../../api/http';

function AssistantSenderInput(props: GetProps<typeof Input.TextArea>) {
  return <Input.TextArea {...props} maxLength={32000} aria-label="给助手的消息"/>;
}

export function AssistantMentionTextarea({ projectId, value, disabled, references = [], onChange, onSubmit, onReference, onSkill, onBrowseSkills, skillMode, modelControl, toolControl, skillControl, action, footer, layout }: {
  projectId: string; value: string; disabled: boolean; onChange: (value: string) => void;
  references?: readonly AssistantMentionReference[];
  onSubmit: () => void; onReference: (reference: AssistantMentionReference) => Promise<boolean>;
  onSkill?: (skill: AgentSkill) => boolean; onBrowseSkills?: () => void; skillMode?: 'standard' | 'infinite_canvas';
  modelControl?: ReactNode; toolControl?: ReactNode; skillControl?: ReactNode; footer?: ReactNode;
  action: { generating: boolean; stopping: boolean; submitting: boolean; disabled: boolean; onStop: () => void };
  layout?: 'standard';
}) {
  const [mention, setMention] = useState<{ kind: 'subject' | 'skill'; start: number; end: number; query: string } | null>(null);
  const [assets, setAssets] = useState<AssistantMentionReference[]>([]);
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<GetRef<typeof Sender>>(null);
  const composing = useRef(false);
  const selecting = useRef(false);
  const restoreFocus = useRef(false);
  const mentionHelpId = useId();
  const current = useRef({ value, mention }); current.current = { value, mention };
  const subjects = [...references.filter(item => item.name.toLowerCase().includes(mention?.query.toLowerCase() ?? '')), ...assets]
    .filter((item, index, all) => all.findIndex(candidate => candidate.kind === item.kind && candidate.id === item.id) === index);
  const availableSkills = skills.filter(item => item.enabled && item.compatible && (!skillMode || item.modes.includes(skillMode)));
  const items = mention?.kind === 'skill'
    ? [...availableSkills.map(item => ({ value: `skill:${item.id}:${item.content_version}`, label: item.name, extra: `${item.builtin ? '内置' : '个人'} · v${item.content_version} · ID ${item.id}` })),
      ...(onBrowseSkills ? [{ value: 'skill:browse', label: '浏览全部 Skill', extra: '搜索全部方法' }] : [])]
    : subjects.map(item => ({ value: `${item.kind}:${item.id}`, label: item.name, extra: item.kind === 'asset' ? '素材' : item.kind === 'shot' ? '分镜' : '节点' }));
  useEffect(() => {
    if (!mention) return;
    const controller = new AbortController(); setLoading(true); setError(''); setAssets([]); setSkills([]);
    const timer = setTimeout(() => {
      if (mention.kind === 'skill') {
        agentsApi.skills(0, controller.signal, { query: mention.query || undefined })
          .then(page => { if (!controller.signal.aborted) setSkills(page.items); })
          .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
          .finally(() => { if (!controller.signal.aborted) setLoading(false); });
      } else assetLibraries.list({ kind: 'project', projectId }, { q: mention.query, offset: 0, limit: 20 }, controller.signal)
        .then(page => { if (!controller.signal.aborted) setAssets(page.items.map(item => ({ kind: 'asset', id: item.id, name: item.name, revision: item.row_version }))); })
        .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [projectId, mention?.kind, mention?.query, !!mention]);
  useEffect(() => {
    if (!adding && restoreFocus.current) { restoreFocus.current = false; input.current?.focus(); }
  }, [adding]);
  function change(text: string, caret: number) {
    onChange(text);
    const match = /(?:^|\s)([@/])([^\s@/]*)$/.exec(text.slice(0, caret));
    setMention(match && (match[1] === '@' || onSkill) ? { kind: match[1] === '/' ? 'skill' : 'subject', start: caret - match[2].length - 1, end: caret, query: match[2] } : null);
  }
  function selectSkill(key: string) {
    if (disabled || adding || !mention) return;
    if (key === 'skill:browse') { onChange(value.slice(0, mention.start) + value.slice(mention.end)); setMention(null); onBrowseSkills?.(); return; }
    const skill = availableSkills.find(item => `skill:${item.id}:${item.content_version}` === key);
    if (skill && onSkill?.(skill)) {
      const caret = mention.start;
      onChange(value.slice(0, caret) + value.slice(mention.end));
      setMention(null);
      requestAnimationFrame(() => { input.current?.focus(); input.current?.nativeElement.querySelector('textarea')?.setSelectionRange(caret, caret); });
    }
  }
  async function select(item: AssistantMentionReference) {
    if (selecting.current || disabled || !mention) return;
    const captured = mention; const original = value; selecting.current = true; restoreFocus.current = true; setAdding(true);
    try {
      if (!await onReference(item)) return;
      if (current.current.value === original && current.current.mention === captured) {
        const inserted = `@${item.name} `;
        onChange(original.slice(0, captured.start) + inserted + original.slice(captured.end)); setMention(null);
      }
    } finally { selecting.current = false; setAdding(false); }
  }
  const suggestionOpen = !!mention && !loading && !error && !!items.length && !disabled && !adding;
  const mentionStatus = mention && (loading || error || !items.length) ? <div className="ai-assistant-mention-status" onKeyDown={event => { if (layout === 'standard' && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setMention(null); input.current?.focus(); } }}>
    <span role={error ? 'alert' : 'status'}>{loading ? mention.kind === 'skill' ? '正在查找方法…' : '正在读取素材…' : error || (mention.kind === 'skill' ? '没有匹配的可用方法' : '没有匹配的项目素材')}</span>
    <Button type="text" size="small" onClick={() => setMention(null)}>关闭引用列表</Button>
  </div> : null;
  const content = <div className="ai-assistant-mention-input" onCompositionStartCapture={() => { composing.current = true; }} onCompositionEndCapture={() => { composing.current = false; }}>
    <span id={mentionHelpId} className="sr-only" role="status">{suggestionOpen ? `找到 ${items.length} 项${mention?.kind === 'skill' ? '方法选项' : '引用'}。使用上下方向键选择，Enter 添加，Escape 关闭。` : `输入 @ 查询并引用项目素材。${onSkill ? '输入 / 选择 Skill，同名时核对来源和身份。' : ''}Enter 发送消息，Shift + Enter 换行。`}</span>
    {layout !== 'standard' && mentionStatus}
    <Suggestion block open={suggestionOpen} rootClassName={`ai-assistant-suggestions${layout === 'standard' ? ' standard-assistant-suggestions' : ''}`}
      items={items}
      onOpenChange={open => { if (!open && !selecting.current) setMention(null); }}
      onSelect={key => { if (mention?.kind === 'skill') selectSkill(key); else { const item = subjects.find(item => `${item.kind}:${item.id}` === key); if (item) void select(item); } }}>
      {({ onKeyDown }) => <Sender ref={input} className="ai-assistant-sender" components={{ input: AssistantSenderInput }}
        autoSize={{ minRows: 3, maxRows: 8 }} value={value} disabled={disabled || adding} loading={action.generating}
        actions={false} header={skillControl} footer={({ components: { SendButton, LoadingButton } }) => <>
          <div className="ai-assistant-sender-footer">{modelControl}{toolControl}
            {action.generating
              ? <LoadingButton className="ai-assistant-send" aria-label="停止" aria-busy={action.stopping} disabled={action.disabled} title={action.stopping ? '正在停止，等待当前操作结束' : '停止回复'} icon={<Square size={12} fill="currentColor"/>}/>
              : <SendButton className="ai-assistant-send" aria-label="发送" disabled={action.disabled || adding || !!mention} loading={action.submitting} title="Enter 发送 · Shift + Enter 换行" icon={<ArrowUp size={16}/>}/>}
          </div>{footer}
        </>} submitType={false} aria-expanded={suggestionOpen} aria-haspopup="menu" aria-describedby={mentionHelpId}
        placeholder={onSkill ? '描述想法，@ 引用素材，/ 选择 Skill' : '描述你的想法，或用 @ 引用素材'}
        onChange={(text, event) => change(text, event?.currentTarget.selectionStart ?? text.length)}
        onSubmit={() => { if (!mention && !action.generating) onSubmit(); }} onCancel={action.onStop}
        onKeyDown={event => {
          if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if (mention && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setMention(null); }
          else if (suggestionOpen && ['ArrowDown', 'ArrowUp'].includes(event.key)) onKeyDown(event);
          else if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            if (mention) { if (suggestionOpen) onKeyDown(event); } else if (!action.generating) onSubmit();
          }
        }}/>}
    </Suggestion>
  </div>;
  return layout === 'standard' ? <Popover trigger={[]} open={!!mentionStatus && !disabled && !adding} content={mentionStatus} placement="topLeft" classNames={{ root: 'standard-assistant-popover standard-creation-popup' }}>{content}</Popover> : content;
}
