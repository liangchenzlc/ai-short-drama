import { extractionDraftError } from '@host/features/creation/assets/extraction/contract';
import { Icon } from '@host/shared/ui/Icon';
import { Alert, Button, Checkbox, Input, Popover, Select } from 'antd';

import { allKinds, importanceLabels, labels } from '@host/features/creation/assets/extraction/presentation';
import type { ExtractionCandidateCardProps } from '@host/types/creation/assets/extraction/views';
import { useState } from 'react';
export function ExtractionCandidateCard({
  item,
  draft,
  disabled,
  stale,
  dirty,
  selected,
  editing,
  choice,
  onSelect,
  onToggle,
  onEdit,
  onKind,
  onChoice,
}: ExtractionCandidateCardProps) {
  const draftError = !item.applied ? extractionDraftError(draft) : '';
  return (
    <article className="extraction-candidate" key={item.candidate_id}>
      <header>
        <Checkbox
          aria-label={`选择 ${draft.name}`}
          disabled={disabled || stale}
          checked={selected && !item.applied}
          onChange={(event) => onSelect(event.target.checked)}
        />
        <h3>{draft.name}</h3>
        <Popover
          overlayClassName="standard-creation-popup"
          trigger="click"
          title="图片生成提示词"
          content={
            <div className="extraction-prompt-popover">
              <Input.TextArea
                aria-label="图片生成提示词"
                value={draft.prompt}
                readOnly={disabled}
                maxLength={8000}
                autoSize={{ minRows: 4, maxRows: 12 }}
                onChange={(event) =>
                  onEdit({
                    prompt: event.target.value,
                  })
                }
              />
            </div>
          }
        >
          <Button
            type="text"
            size="small"
            aria-label={`查看 ${draft.name} 的图片生成提示词`}
            icon={<Icon name="film" size={18} />}
          />
        </Popover>
        {item.applied ? (
          <span className="status-badge is-success">已加入本集</span>
        ) : (
          <Button type="text" disabled={disabled} onClick={() => onToggle()}>
            {editing ? '收起编辑' : '编辑'}
          </Button>
        )}
      </header>
      {item.original.story_function && (
        <section className="extraction-story-function" aria-label="剧情作用">
          <span className={`extraction-importance is-${item.original.importance ?? 'continuity'}`}>
            {importanceLabels[item.original.importance ?? 'continuity']}
          </span>
          <div>
            <h4>剧情作用</h4>
            <p>{item.original.story_function}</p>
          </div>
        </section>
      )}
      {editing ? (
        <div className="extraction-editor">
          <label>
            名称
            <Input
              aria-label="素材名称"
              maxLength={255}
              value={draft.name}
              disabled={disabled}
              onChange={(event) =>
                onEdit({
                  name: event.target.value,
                })
              }
            />
          </label>
          <label>
            类型
            <Select
              classNames={{
                popup: { root: 'standard-creation-popup' },
              }}
              aria-label="素材类型"
              value={draft.kind}
              disabled={disabled}
              options={allKinds.map((value) => ({
                value,
                label: labels[value],
              }))}
              onChange={(kind) => {
                onEdit({
                  kind,
                  scene_time: kind === 'scene' ? draft.scene_time : '',
                });
                onKind(kind);
              }}
            />
          </label>
          <label>
            描述
            <Input.TextArea
              aria-label="素材描述"
              autoSize={{ minRows: 2, maxRows: 8 }}
              maxLength={8000}
              value={draft.description}
              disabled={disabled}
              onChange={(event) =>
                onEdit({
                  description: event.target.value,
                })
              }
            />
          </label>

          {draft.kind === 'scene' && (
            <label>
              场景时间
              <Input
                maxLength={60}
                value={draft.scene_time}
                disabled={disabled}
                onChange={(event) =>
                  onEdit({
                    scene_time: event.target.value,
                  })
                }
              />
            </label>
          )}
          <label>
            标签
            <Select
              classNames={{
                popup: { root: 'standard-creation-popup' },
              }}
              aria-label="素材标签"
              mode="tags"
              value={draft.tags}
              disabled={disabled}
              maxCount={20}
              onChange={(tags) => onEdit({ tags })}
            />
          </label>
        </div>
      ) : (
        <div className="extraction-copy">
          <div>
            <h4>描述</h4>
            <ExtractionText text={draft.description} />
          </div>
        </div>
      )}
      {item.original.aliases.length > 0 && <p className="creation-help">别名：{item.original.aliases.join('、')}</p>}
      {draftError && <Alert type="error" showIcon message={draftError} />}
      {!item.applied && (
        <div className="extraction-adoption">
          <label>
            采用方式
            <Select
              classNames={{
                popup: { root: 'standard-creation-popup' },
              }}
              aria-label={`${draft.name}的采用方式`}
              disabled={disabled || dirty}
              value={choice || undefined}
              placeholder={dirty ? '保存候选后核对匹配' : '请选择新建或复用'}
              options={[
                {
                  value: 'create',
                  label: item.matches.length || item.duplicate_candidates?.length ? '明确另建素材' : '新建素材',
                },
                ...item.matches.map((match) => ({
                  value: match.asset_id,
                  label: `复用素材：${match.name}`,
                })),
              ]}
              onChange={(value) => onChoice(value)}
            />
          </label>
          {choice && choice !== 'create' && <p>沿用已有素材的描述、提示词与图片，提取文字保留在本次记录中。</p>}
          {item.matches.length > 0 && (
            <p>
              {item.matches.length > 1 || !item.matches[0].exact
                ? '发现疑似重复素材，请核对后选择。'
                : '发现同名素材，可复用或明确另建。'}
            </p>
          )}
          {!!item.duplicate_candidates?.length && <p>本次结果还有同名候选，请只选择需要的项，或修改名称后明确另建。</p>}
        </div>
      )}
    </article>
  );
}

function ExtractionText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 240 || text.split('\n').length > 6;
  return (
    <>
      <p className={long && !expanded ? 'extraction-text-clamped' : undefined}>{text}</p>
      {long && (
        <Button type="link" size="small" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
          {expanded ? '收起' : '展开全文'}
        </Button>
      )}
    </>
  );
}
