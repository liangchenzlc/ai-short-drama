import type { ShotRead } from '@host/types/creation/storyboard/shots';
import { Button, Checkbox, Dropdown } from 'antd';
import { Check, Ellipsis, Image, Trash2 } from 'lucide-react';
import { useId, useRef, useState } from 'react';
export function StoryboardShotCard({
  shot,
  selected,
  disabled,
  batch,
  checked,
  onSelect,
  onCheck,
  onDelete,
  deleting = false,
  deleteDisabled = false,
  media = 'image',
}: {
  shot: ShotRead;
  selected: boolean;
  disabled: boolean;
  batch: boolean;
  checked: boolean;
  onSelect: () => void;
  onCheck: (checked: boolean) => void;
  onDelete?: () => void;
  deleting?: boolean;
  deleteDisabled?: boolean;
  media?: 'image' | 'video';
}) {
  const descriptionId = useId();
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsTrigger = useRef<HTMLButtonElement | HTMLAnchorElement>(null);
  const imageUrl = shot.image?.url;
  const current = media === 'video' ? shot.video : shot.image;
  const size = {
    full_shot: '全景',
    medium_shot: '中景',
    close_up: '近景',
    extreme_close_up: '特写',
    wide_shot: '远景',
    two_shot: '双人镜头',
  };
  const position = String(shot.position).padStart(2, '0');
  return (
    <article
      id={`storyboard-shot-${shot.id}`}
      className={`storyboard-item storyboard-shot-card${selected ? ' is-selected' : ''}${onDelete ? ' has-card-actions' : ''}`}
      aria-busy={deleting}
    >
      {batch && (
        <Checkbox
          className="batch-item-select"
          aria-label={`批量选择分镜 ${shot.position}`}
          checked={checked}
          disabled={disabled}
          onChange={(event) => onCheck(event.target.checked)}
        />
      )}
      <button
        type="button"
        className="storyboard-summary"
        aria-label={`选择分镜 ${position}`}
        aria-describedby={descriptionId}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onSelect}
      >
        <span className="storyboard-card-media">
          <span className="standard-shot-number">{position}</span>
          <span className="standard-shot-duration">{shot.duration_ms / 1000} 秒</span>
          {!current && <span className="standard-shot-pending">待制作</span>}
          {selected && (
            <span className="standard-shot-selection" aria-label="当前选中">
              <Check size={14} />
            </span>
          )}
          {imageUrl && failedUrl !== imageUrl ? (
            <img
              src={imageUrl}
              alt={`分镜 ${position} 的当前主图`}
              loading="lazy"
              decoding="async"
              onError={() => setFailedUrl(imageUrl)}
            />
          ) : (
            <span className="storyboard-card-placeholder">
              <Image size={30} aria-hidden="true" />
              <span className="sr-only">{imageUrl ? '图片暂时无法预览' : '尚无主图'}</span>
              <small className="sr-only">
                {imageUrl ? '打开分镜图核对当前图片' : shot.script.trim() ? '选择镜头后制作分镜图' : '先编写本镜脚本'}
              </small>
            </span>
          )}
        </span>
        <span className="storyboard-card-copy">
          <span className="storyboard-card-heading">
            <strong title={shot.title || '未命名镜头'}>{shot.title || '未命名镜头'}</strong>
            <span>{shot.shot_size ? size[shot.shot_size] : ''}</span>
          </span>
        </span>
        <span id={descriptionId} className="sr-only">
          {shot.script || '尚未编写镜头脚本'} · {shot.duration_ms / 1000} 秒 · 图片：{shot.image ? '已生图' : '待制作'}{' '}
          · 视频：
          {shot.video ? '已采用' : '待制作'}
        </span>
      </button>
      {onDelete && (
        <Dropdown
          trigger={['click']}
          placement="bottomRight"
          overlayClassName="standard-creation-popup"
          open={actionsOpen && !deleteDisabled}
          onOpenChange={setActionsOpen}
          menu={{
            items: [
              {
                key: 'delete',
                label: '删除分镜',
                icon: <Trash2 size={14} />,
                danger: true,
              },
            ],
            onClick: ({ domEvent }) => {
              domEvent.stopPropagation();
              setActionsOpen(false);
              onDelete();
            },
            onKeyDown: (event) => {
              if (event.key !== 'Escape') return;
              event.preventDefault();
              event.stopPropagation();
              setActionsOpen(false);
              actionsTrigger.current?.focus();
            },
          }}
        >
          <Button
            ref={actionsTrigger}
            className="storyboard-card-actions"
            type="text"
            aria-label={`分镜 ${position} 卡片更多操作`}
            title="更多操作"
            aria-haspopup="menu"
            aria-expanded={actionsOpen && !deleteDisabled}
            disabled={deleteDisabled}
            loading={deleting}
            icon={<Ellipsis size={16} />}
            onClick={(event) => event.stopPropagation()}
          />
        </Dropdown>
      )}
    </article>
  );
}
