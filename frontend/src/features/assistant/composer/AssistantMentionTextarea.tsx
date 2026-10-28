import { Sender, Suggestion } from '@ant-design/x';
import { projectAssetsApi } from '@host/api/creation/assets/management';
import { errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { ComposerAttachmentsContext } from '@host/features/assistant/attachments/agent-composer-context';
import { AssistantPromptInput, PromptTokenProvider } from '@host/features/assistant/composer/AssistantPromptField';
import type { AssistantMentionReference } from '@host/types/assistant/api';
import type { MentionReference } from '@host/types/assistant/composer/AssistantMentionTextarea';
import type { AgentSkill } from '@host/types/assistant/engine';
import { Button, Popover, type GetRef } from 'antd';
import { ArrowUp, Square } from 'lucide-react';
import { useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
export function AssistantMentionTextarea({
  projectId,
  value,
  disabled,
  references = [],
  highlights = [],
  onChange,
  onSubmit,
  onReference,
  onSkill,
  onBrowseSkills,
  skillMode,
  modelControl,
  toolControl,
  footer,
  action,
  layout,
}: {
  projectId: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  references?: readonly AssistantMentionReference[];
  highlights?: readonly string[];
  onSubmit: () => void;
  onReference: (reference: AssistantMentionReference) => Promise<boolean>;
  onSkill?: (skill: AgentSkill) => boolean;
  onBrowseSkills?: () => void;
  skillMode?: 'standard' | 'infinite_canvas';
  modelControl?: ReactNode;
  toolControl?: ReactNode;
  footer?: ReactNode;
  action: {
    generating: boolean;
    stopping: boolean;
    submitting: boolean;
    disabled: boolean;
    onStop: () => void;
    canStop?: boolean;
    canInterject?: boolean;
    interjecting?: boolean;
    onInterject?: () => void;
  };
  layout?: 'standard';
}) {
  const [mention, setMention] = useState<{
    kind: 'subject' | 'skill';
    start: number;
    end: number;
    query: string;
  } | null>(null);
  const [assets, setAssets] = useState<MentionReference[]>([]);
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<GetRef<typeof Sender>>(null);
  const composing = useRef(false);
  const selecting = useRef(false);
  const restoreFocus = useRef(false);
  const mentionHelpId = useId();
  const attachments = useContext(ComposerAttachmentsContext);
  const current = useRef({ value, mention });
  current.current = { value, mention };
  const query = mention?.query.toLowerCase() ?? '';
  const subjects: MentionReference[] = [
    ...references.filter((item) => item.name.toLowerCase().includes(query)),
    ...assets,
  ].filter(
    (item, index, all) =>
      all.findIndex((candidate) => candidate.kind === item.kind && candidate.id === item.id) === index,
  );
  const availableSkills = skills.filter(
    (item) => item.enabled && item.compatible && (!skillMode || item.modes.includes(skillMode)),
  );
  const items =
    mention?.kind === 'skill'
      ? [
          ...availableSkills.map((item) => ({
            value: `skill:${item.id}:${item.content_version}`,
            label: skillLabel(item.name, item.description),
          })),
          ...(onBrowseSkills
            ? [
                {
                  value: 'skill:browse',
                  label: skillLabel('浏览全部 Skill', '按名称、用途或标签查找'),
                },
              ]
            : []),
        ]
      : [
          ...attachments
            .filter((item) => item.name.toLowerCase().includes(query))
            .map((item) => ({
              value: `attachment:${item.id}`,
              label: item.name,
              icon:
                item.kind === 'image' && item.url ? (
                  <img className="ai-assistant-mention-thumb" src={item.url} alt="" loading="lazy" decoding="async" />
                ) : undefined,
              extra: '附件',
            })),
          ...subjects.map((item) => ({
            value: `${item.kind}:${item.id}`,
            label: item.name,
            icon: item.thumbnail ? (
              <img className="ai-assistant-mention-thumb" src={item.thumbnail} alt="" loading="lazy" decoding="async" />
            ) : undefined,
            extra: item.kind === 'asset' ? '素材' : item.kind === 'shot' ? '分镜' : '节点',
          })),
        ];
  useEffect(() => {
    if (!mention) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setAssets([]);
    setSkills([]);
    const timer = setTimeout(() => {
      if (mention.kind === 'skill') {
        skillsApi
          .skills(0, controller.signal, { query: mention.query || undefined })
          .then((page) => {
            if (!controller.signal.aborted) setSkills(page.items);
          })
          .catch((cause) => {
            if (!controller.signal.aborted) setError(errorMessage(cause));
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      } else
        projectAssetsApi
          .list({ kind: 'project', projectId }, { q: mention.query, offset: 0, limit: 20 }, controller.signal)
          .then((page) => {
            if (!controller.signal.aborted)
              setAssets(
                page.items.map((item) => ({
                  kind: 'asset',
                  id: item.id,
                  name: item.name,
                  revision: item.row_version,
                  thumbnail: item.image?.url ?? null,
                })),
              );
          })
          .catch((cause) => {
            if (!controller.signal.aborted) setError(errorMessage(cause));
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
    }, 150);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [projectId, mention?.kind, mention?.query, !!mention]);
  useEffect(() => {
    if (!adding && restoreFocus.current) {
      restoreFocus.current = false;
      input.current?.focus();
    }
  }, [adding]);
  function change(text: string, caret: number) {
    onChange(text);
    const match = /(?:^|\s)([@/])([^\s@/]*)$/.exec(text.slice(0, caret));
    setMention(
      match && (match[1] === '@' || onSkill)
        ? {
            kind: match[1] === '/' ? 'skill' : 'subject',
            start: caret - match[2].length - 1,
            end: caret,
            query: match[2],
          }
        : null,
    );
  }
  function placeCaret(position: number) {
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.nativeElement.querySelector('textarea')?.setSelectionRange(position, position);
    });
  }
  function selectSkill(key: string) {
    if (disabled || adding || !mention) return;
    if (key === 'skill:browse') {
      onChange(value.slice(0, mention.start) + value.slice(mention.end));
      setMention(null);
      onBrowseSkills?.();
      return;
    }
    const skill = availableSkills.find((item) => `skill:${item.id}:${item.content_version}` === key);
    if (!skill || !onSkill?.(skill)) return;
    // 选中的方法以 /名称 留在正文里，并由高亮层标出；顶栏不再单列标签。
    const inserted = `/${skill.name} `;
    const caret = mention.start + inserted.length;
    onChange(value.slice(0, mention.start) + inserted + value.slice(mention.end));
    setMention(null);
    placeCaret(caret);
  }
  function selectAttachment(key: string) {
    if (disabled || adding || !mention) return;
    const item = attachments.find((candidate) => `attachment:${candidate.id}` === key);
    if (!item) return;
    // 已添加的素材只写一个可读引用；它本身已在 attachment_ids 里，不重复建附件。
    const inserted = `@${item.name} `;
    const caret = mention.start + inserted.length;
    onChange(value.slice(0, mention.start) + inserted + value.slice(mention.end));
    setMention(null);
    placeCaret(caret);
  }
  async function select(item: AssistantMentionReference) {
    if (selecting.current || disabled || !mention) return;
    const captured = mention;
    const original = value;
    selecting.current = true;
    restoreFocus.current = true;
    setAdding(true);
    try {
      if (!(await onReference(item))) return;
      if (current.current.value === original && current.current.mention === captured) {
        const inserted = `@${item.name} `;
        onChange(original.slice(0, captured.start) + inserted + original.slice(captured.end));
        setMention(null);
        placeCaret(captured.start + inserted.length);
      }
    } finally {
      selecting.current = false;
      setAdding(false);
    }
  }
  const suggestionOpen = !!mention && !loading && !error && !!items.length && !disabled && !adding;
  const mentionStatus =
    mention && (loading || error || !items.length) ? (
      <div
        className="ai-assistant-mention-status"
        onKeyDown={(event) => {
          if (layout === 'standard' && event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            setMention(null);
            input.current?.focus();
          }
        }}
      >
        <span role={error ? 'alert' : 'status'}>
          {loading
            ? mention.kind === 'skill'
              ? '正在查找方法…'
              : '正在读取素材…'
            : error || (mention.kind === 'skill' ? '没有匹配的可用方法' : '没有匹配的项目素材')}
        </span>
        <Button type="text" size="small" onClick={() => setMention(null)}>
          关闭引用列表
        </Button>
      </div>
    ) : null;
  const content = (
    <div
      className="ai-assistant-mention-input"
      onCompositionStartCapture={() => {
        composing.current = true;
      }}
      onCompositionEndCapture={() => {
        composing.current = false;
      }}
    >
      <span id={mentionHelpId} className="sr-only" role="status">
        {suggestionOpen
          ? `找到 ${items.length} 项${mention?.kind === 'skill' ? '方法选项' : '引用'}。使用上下方向键选择，Enter 添加，Escape 关闭。`
          : `输入 @ 查询并引用素材，${onSkill ? '输入 / 选择 Skill。' : ''}Enter 发送消息，Shift + Enter 换行。`}
      </span>
      {layout !== 'standard' && mentionStatus}
      <PromptTokenProvider tokens={[...highlights, ...attachments.map((item) => `@${item.name}`)]}>
        <Suggestion
          block
          open={suggestionOpen}
          rootClassName={`ai-assistant-suggestions${layout === 'standard' ? ' standard-assistant-suggestions' : ''}`}
          items={items}
          onOpenChange={(open) => {
            if (!open && !selecting.current) setMention(null);
          }}
          onSelect={(key) => {
            if (mention?.kind === 'skill') selectSkill(key);
            else if (key.startsWith('attachment:')) selectAttachment(key);
            else {
              const item = subjects.find((item) => `${item.kind}:${item.id}` === key);
              if (item) void select(item);
            }
          }}
        >
          {({ onKeyDown }) => (
            <Sender
              ref={input}
              className="ai-assistant-sender"
              components={{ input: AssistantPromptInput }}
              autoSize={{ minRows: 3, maxRows: 8 }}
              value={value}
              disabled={disabled || adding}
              loading={action.generating}
              actions={false}
              footer={({ components: { SendButton, LoadingButton } }) => (
                <>
                  <div className="ai-assistant-sender-footer">
                    {modelControl}
                    {toolControl}
                    {action.generating ? (
                      <>
                        {action.canInterject && (
                          <Button
                            className="ai-assistant-send"
                            aria-label="发送补充要求"
                            disabled={adding || !!mention}
                            loading={action.interjecting}
                            title="补充要求将在下一步骤生效 · Enter"
                            onClick={() => action.onInterject?.()}
                            icon={<ArrowUp size={16} />}
                          />
                        )}
                        {action.canStop !== false ? (
                          <LoadingButton
                            className="ai-assistant-send"
                            aria-label="停止"
                            aria-busy={action.stopping}
                            disabled={action.disabled}
                            title={action.stopping ? '正在停止，等待当前操作结束' : '停止回复'}
                            icon={<Square size={12} fill="currentColor" />}
                          />
                        ) : (
                          <span className="ai-assistant-meta" role="status">
                            正在创作…
                          </span>
                        )}
                      </>
                    ) : (
                      <SendButton
                        className="ai-assistant-send"
                        aria-label="发送"
                        disabled={action.disabled || adding || !!mention}
                        loading={action.submitting}
                        title="Enter 发送 · Shift + Enter 换行"
                        icon={<ArrowUp size={16} />}
                      />
                    )}
                  </div>
                  {footer}
                </>
              )}
              submitType={false}
              aria-expanded={suggestionOpen}
              aria-haspopup="menu"
              aria-describedby={mentionHelpId}
              placeholder={onSkill ? '描述想法，@ 引用素材，/ 选择 Skill' : '描述你的想法，或用 @ 引用素材'}
              onChange={(text, event) => change(text, event?.currentTarget.selectionStart ?? text.length)}
              onSubmit={() => {
                if (mention) return;
                if (action.generating) {
                  if (action.canInterject) action.onInterject?.();
                  return;
                }
                onSubmit();
              }}
              onCancel={() => {
                if (action.canStop !== false) action.onStop();
              }}
              onKeyDown={(event) => {
                if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
                if (mention && event.key === 'Escape') {
                  event.preventDefault();
                  event.stopPropagation();
                  setMention(null);
                } else if (suggestionOpen && ['ArrowDown', 'ArrowUp'].includes(event.key)) onKeyDown(event);
                else if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  if (mention) {
                    if (suggestionOpen) onKeyDown(event);
                  } else if (action.generating) {
                    if (action.canInterject) action.onInterject?.();
                  } else onSubmit();
                }
              }}
            />
          )}
        </Suggestion>
      </PromptTokenProvider>
    </div>
  );
  return layout === 'standard' ? (
    <Popover
      trigger={[]}
      open={!!mentionStatus && !disabled && !adding}
      content={mentionStatus}
      placement="topLeft"
      classNames={{
        root: 'standard-assistant-popover standard-creation-popup',
      }}
    >
      {content}
    </Popover>
  ) : (
    content
  );
}

function skillLabel(name: string, description: string) {
  return (
    <span className="ai-assistant-skill-option">
      <span className="ai-assistant-skill-option-name">{name}</span>
      {description ? <span className="ai-assistant-skill-option-desc">{description}</span> : null}
    </span>
  );
}
