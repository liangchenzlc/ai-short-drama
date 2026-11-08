import { errorMessage } from '@host/api/http';
import { imageGroups } from '@host/api/media/image-groups';
import { ImageToolsDialog, imageToolLabels } from '@host/features/media/image-tools/ImageToolsDialog';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { Dialog } from '@host/shared/ui/Dialog';
import { ImagePreview } from '@host/shared/ui/ImagePreview';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { ImageGroupItem, ImageMedia, ImageTool } from '@host/types/media/image-groups';
import type { ImageGroupPreviewProps } from '@host/types/media/image-groups/ImageGroupPreview';
import { Button, Dropdown, Spin } from 'antd';
import { Check, Expand, Image as ImageIcon, MoreHorizontal, Pencil, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import '../image-tools/image-tools.css';
import './imageGroupGallery.css';
export function ImageGroupPreview({
  group,
  loading = false,
  error = '',
  label,
  disabled,
  imageModelId,
  onRefresh,
  onBeforeChange,
  onChanged,
  onAddReference,
  onUseForVideo,
  onBusyChange,
  emptyAction,
  emptyCount = 1,
  historical = false,
  primaryStale = false,
  currentImage,
  presentation,
}: ImageGroupPreviewProps) {
  const [tool, setTool] = useState<{
    item: ImageGroupItem;
    tool: ImageTool;
  } | null>(null);
  const [preview, setPreview] = useState<{ src: string; alt: string } | null>(null);
  const [history, setHistory] = useState<ImageGroupItem | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [message, setMessage] = useState<{
    type: 'success' | 'error';
    text: string;
  } | null>(null);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(!!busyItem);
    return () => onBusyChange?.(false);
  }, [busyItem, onBusyChange]);
  const changed = () => {
    onChanged();
    onRefresh();
  };
  async function perform(item: ImageGroupItem, action: () => Promise<void>, mutation = true) {
    if (lock.current || (mutation && disabled)) return;
    lock.current = true;
    setBusyItem(item.id);
    setMessage(null);
    try {
      await action();
    } catch (cause) {
      if (alive.current) setMessage({ type: 'error', text: errorMessage(cause) });
    } finally {
      lock.current = false;
      if (alive.current) setBusyItem(null);
    }
  }
  async function primary(item: ImageGroupItem) {
    if (!group) return;
    await perform(item, async () => {
      if (!(await onBeforeChange())) return;
      const latest = await imageGroups.detail(group.id);
      await imageGroups.primary({ ...group, target_version: latest.target_version }, item);
      if (alive.current) {
        setMessage({
          type: 'success',
          text: `第 ${item.output_index} 张已设为主图。`,
        });
        changed();
      }
    });
  }
  async function download(item: ImageGroupItem) {
    if (!item.media_id) return;
    await perform(
      item,
      async () => {
        const blob = await imageGroups.content(item.media_id!);
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        const ext = blob.type === 'image/jpeg' ? 'jpg' : blob.type === 'image/webp' ? 'webp' : 'png';
        link.download = `${label}-${item.output_index}.${ext}`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
      false,
    );
  }
  async function restore(item: ImageGroupItem, media: ImageMedia) {
    await perform(item, async () => {
      if (!(await onBeforeChange())) return;
      const body = {
        item_id: item.id,
        expected_item_version: item.item_version,
        operation_type: 'replace' as const,
        parameters: {},
        restore_media_id: media.media_id,
      };
      const scope = `image-restore:${item.id}:${media.media_id}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      const operation = await imageGroups.create(body, key);
      await imageGroups.execute(operation.id);
      clearAttempt(scope, attemptStorage());
      if (alive.current) {
        setHistory(null);
        setMessage({ type: 'success', text: '原图已恢复到此位置。' });
        changed();
      }
    });
  }
  function editMenu(item: ImageGroupItem) {
    return [
      ...(['replace', 'crop', 'annotation', 'resize', 'split'] as ImageTool[]).map((value) => ({
        key: value,
        label: imageToolLabels[value],
        disabled: disabled || !!busyItem,
        onClick: () => setTool({ item, tool: value }),
      })),
      { type: 'divider' as const },
      ...(['mask', 'emotion', 'texture', 'angle', 'lighting'] as ImageTool[]).map((value) => ({
        key: value,
        label: imageToolLabels[value],
        disabled: disabled || !!busyItem,
        onClick: () => setTool({ item, tool: value }),
      })),
    ];
  }
  function menu(item: ImageGroupItem) {
    return [
      {
        key: 'download',
        label: '下载图片',
        disabled: !!busyItem,
        onClick: () => void download(item),
      },
      { type: 'divider' as const },
      ...(!historical
        ? [
            {
              key: 'reverse_prompt',
              label: imageToolLabels.reverse_prompt,
              disabled: disabled || !!busyItem,
              onClick: () => setTool({ item, tool: 'reverse_prompt' }),
            },
          ]
        : []),
      {
        key: 'copy',
        label: '复制提示词',
        disabled: !item.prompt,
        onClick: () => {
          void navigator.clipboard
            .writeText(item.prompt ?? '')
            .then(() => setMessage({ type: 'success', text: '提示词已复制。' }))
            .catch(() =>
              setMessage({
                type: 'error',
                text: '复制失败，请在本人的生成任务详情中复制提示词。',
              }),
            );
        },
      },
      ...(onAddReference
        ? [
            {
              key: 'reference',
              label: '加入私人参考图',
              disabled: disabled || !!busyItem,
              onClick: () =>
                void perform(item, async () => {
                  await onAddReference(item.media_id!);
                  setMessage({ type: 'success', text: '已加入私人参考图。' });
                }),
            },
          ]
        : []),
      ...(onUseForVideo
        ? [{ key: 'video_reference', label: '用作视频参考', disabled: disabled || !item.media_id,
            onClick: () => onUseForVideo(item.media_id!) }]
        : []),
      { type: 'divider' as const },
      {
        key: 'history',
        label: `原图历史${item.history.length ? `（${item.history.length}）` : ''}`,
        disabled: !item.history.length,
        onClick: () => setHistory(item),
      },
    ];
  }
  const assetCreation = presentation === 'asset-creation';
  const count = group?.expected_count ?? (assetCreation ? 1 : [1, 2, 4].includes(emptyCount) ? emptyCount : 1);
  const chosenItem = group?.items.find((item) => item.id === selectedItemId);
  const selectedItem = chosenItem
    ?? group?.items.find((item) => item.output_index === group.primary_index)
    ?? group?.items.find((item) => item.status === 'ready')
    ?? group?.items[0];
  const separatePrimary = !!(assetCreation && group && currentImage &&
    !group.items.some((item) => item.media_id === currentImage.media_id));
  const isCurrentPrimary = (item: ImageGroupItem) => !!group?.is_current &&
    group.primary_index === item.output_index && (!currentImage || item.media_id === currentImage.media_id);
  const showCurrent = separatePrimary && (selectedItemId === 'current' || !chosenItem);
  const displayedItems = assetCreation && group && (group.items.length > 1 || separatePrimary)
    ? group.items.filter((item) => item.id === selectedItem?.id)
    : group?.items;
  const groupStatus = group?.status === 'partial' ? '图片部分完成' : group?.status === 'failed' ? '图片未完成' : '';
  return (
    <div
      className={`standard-image-group${assetCreation ? ' is-asset-creation' : ''}`}
      aria-label={`${label}图片组`}
      aria-busy={loading}
    >
      {error && (
        <StudioFeedback
          type="error"
          title={`${label} · 图片组`}
          noticeKey={`image-group-read:${group?.id ?? label}`}
          message={error}
          actions={
            <Button size="small" onClick={onRefresh}>
              重新读取图片
            </Button>
          }
        />
      )}
      {group?.notice && (
        <StudioFeedback
          type="warning"
          quiet
          title={`${label} · 图片组`}
          noticeKey={`image-group-notice:${group.id}`}
          message={group.notice}
        />
      )}
      {assetCreation && groupStatus && (
        <StudioFeedback
          type="warning"
          quiet
          title={`${label} · 图片组`}
          noticeKey={`image-group-status:${group?.id}`}
          message={groupStatus}
          actions={
            <Button size="small" onClick={onRefresh}>
              重新读取图片
            </Button>
          }
        />
      )}
      <div className={`image-subject-grid image-subject-grid-${count > 4 ? 'many' : count}`} data-image-count={count}>
        {showCurrent && currentImage ? (
          <article className="image-subject is-primary" aria-label="当前主图">
            <div className="image-subject-label"><span className="image-primary-badge"><Check size={12} />主图</span></div>
            <img src={currentImage.url} alt="当前主图" />
            <button type="button" className="image-subject-open" aria-label="放大当前主图"
              onClick={() => setPreview({ src: currentImage.url, alt: '当前主图' })} />
          </article>
        ) : group
          ? displayedItems?.map((item) => (
              <ImageSubject
                key={item.id}
                item={item}
                label={label}
                primary={isCurrentPrimary(item)}
                onRetry={assetCreation ? onRefresh : undefined}
              >
                <div className="image-subject-toolbar" role="toolbar" aria-label={`第 ${item.output_index} 张图片工具`}>
                  <Button
                    type="text"
                    title="全屏预览"
                    aria-label={`预览第 ${item.output_index} 张图片`}
                    icon={<Expand size={15} />}
                    onClick={() => {
                      if (item.url)
                        setPreview({
                          src: item.url,
                          alt: `${label}第 ${item.output_index} 张`,
                        });
                    }}
                  />
                  {(primaryStale || !isCurrentPrimary(item)) && (
                    <Button
                      type="text"
                      disabled={disabled || !!busyItem}
                      title="设为主图"
                      aria-label={`将第 ${item.output_index} 张设为主图`}
                      icon={<Check size={15} />}
                      loading={busyItem === item.id}
                      onClick={() => void primary(item)}
                    />
                  )}
                  {!historical && (
                    <Dropdown
                      trigger={['click']}
                      overlayClassName="standard-creation-popup image-edit-menu"
                      menu={{ items: editMenu(item) }}
                    >
                      <Button
                        type="text"
                        aria-label={`编辑第 ${item.output_index} 张图片`}
                        title="编辑图片"
                        icon={<Pencil size={15} />}
                        aria-haspopup="menu"
                      />
                    </Dropdown>
                  )}
                  <Dropdown trigger={['click']} overlayClassName="standard-creation-popup" menu={{ items: menu(item) }}>
                    <Button
                      type="text"
                      aria-label={`第 ${item.output_index} 张图片更多工具`}
                      title="更多图片操作"
                      icon={<MoreHorizontal size={17} />}
                      aria-haspopup="menu"
                    />
                  </Dropdown>
                </div>
                <button
                  type="button"
                  className="image-subject-open"
                  aria-label={`打开${label}第 ${item.output_index} 张`}
                  onClick={() => {
                    if (item.url)
                      setPreview({
                        src: item.url,
                        alt: `${label}第 ${item.output_index} 张`,
                      });
                  }}
                />
              </ImageSubject>
            ))
          : Array.from({ length: count }, (_, index) => (
              <div
                className="image-subject image-subject-empty"
                key={index}
                aria-label={`${label}第 ${index + 1} 张占位`}
              >
                {!assetCreation && (
                  <div className="image-subject-label">
                    <span>{index + 1}</span>
                  </div>
                )}
                {loading ? (
                  <div className="image-subject-state" role="status">
                    <Spin />
                    <span>正在读取图片…</span>
                  </div>
                ) : (
                  <div className="image-subject-state">
                    {assetCreation ? (
                      <span>还没有生成图片</span>
                    ) : (
                      <>
                        <ImageIcon size={28} />
                        <strong>还没有图片</strong>
                        <p>
                          {`第 ${index + 1} 张图片将在生成后显示。`}
                        </p>
                        {index === 0 && emptyAction}
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
      </div>
      {assetCreation && group && (group.items.length > 1 || separatePrimary ||
        !!selectedItem?.media_id && (primaryStale || !isCurrentPrimary(selectedItem))) && (
        <div className="imageGalleryControls">
          <div className="imageGalleryThumbs">
            {(group.items.length > 1 || separatePrimary) && group.items.map((item) => (
              <button
                type="button"
                key={item.id}
                className={!showCurrent && item.id === selectedItem?.id ? 'isSelected' : undefined}
                aria-label={isCurrentPrimary(item)
                  ? `图片 ${item.output_index}，主图`
                  : `查看图片 ${item.output_index}`}
                onClick={() => setSelectedItemId(item.id)}
              >
                {item.url ? <img src={item.url} alt="" /> : <span>{item.status === 'generating' ? '生成中' : '未完成'}</span>}
                {isCurrentPrimary(item) && <i>主图</i>}
              </button>
            ))}
            {separatePrimary && currentImage && (
              <button type="button" aria-label="查看当前主图" className={showCurrent ? 'isSelected' : undefined}
                onClick={() => setSelectedItemId('current')}>
                <img src={currentImage.url} alt="" /><i>主图</i>
              </button>
            )}
          </div>
          <div className="imageGalleryActions">
            {group.items.length > 1 && <Button type="text" size="small" onClick={() => setShowAll(true)}>查看全部</Button>}
            {!showCurrent && selectedItem?.status === 'ready' && selectedItem.media_id &&
              (primaryStale || !isCurrentPrimary(selectedItem)) && (
                <Button size="small" disabled={disabled || !!busyItem} onClick={() => void primary(selectedItem)}>设为主图</Button>
              )}
          </div>
        </div>
      )}
      {group && !assetCreation && (
        <div className="image-group-caption">
          <span>
            {group.items.filter((item) => item.status === 'ready').length} / {count} 张
            {group.status === 'generating'
              ? ' · 正在生成'
              : group.status === 'partial'
                ? ' · 部分完成'
                : group.status === 'failed'
                  ? ' · 未完成'
                  : ''}
          </span>
          <Button
            type="text"
            size="small"
            aria-label="刷新图片组"
            title="刷新图片组"
            icon={<RefreshCw size={13} />}
            onClick={onRefresh}
          />
        </div>
      )}
      {message && <StudioFeedback type={message.type} title={`${label} · 图片操作`} message={message.text} />}
      {preview && (
        <ImagePreview
          presentation="standard-creation"
          src={preview.src}
          alt={preview.alt}
          onClose={() => setPreview(null)}
        />
      )}
      {showAll && group && (
        <Dialog title="全部图片" className="imageGalleryCompareDialog standard-creation-dialog" onClose={() => setShowAll(false)}>
          <div className="imageGalleryCompare">
            {group.items.map((item) => (
              <button type="button" key={item.id} onClick={() => { setSelectedItemId(item.id); setShowAll(false); }}>
                {item.url ? <img src={item.url} alt={`图片 ${item.output_index}`} /> : <span>{item.status === 'generating' ? '生成中' : '未完成'}</span>}
                <span>图片 {item.output_index}{isCurrentPrimary(item) ? ' · 主图' : ''}</span>
              </button>
            ))}
          </div>
        </Dialog>
      )}
      {tool && (
        <ImageToolsDialog
          key={`${tool.item.id}:${tool.item.item_version}:${tool.tool}`}
          item={tool.item}
          tool={tool.tool}
          imageModelId={imageModelId}
          disabled={disabled}
          onBeforeChange={onBeforeChange}
          onChanged={changed}
          onClose={() => setTool(null)}
          onBusyChange={onBusyChange}
        />
      )}
      {history && (
        <Dialog
          title={`第 ${history.output_index} 张 · 原图历史`}
          className="image-item-history-dialog standard-creation-dialog"
          canClose={!busyItem}
          onClose={() => setHistory(null)}
        >
          <div className="image-item-history">
            {[...history.history].reverse().map((media, index) => (
              <article key={`${media.media_id}:${index}`}>
                {media.url && (
                  <button
                    type="button"
                    className="image-history-preview"
                    onClick={() => setPreview({ src: media.url!, alt: '处理前原图' })}
                  >
                    <img src={media.url} alt="处理前原图" />
                  </button>
                )}
                <span>
                  {media.width} × {media.height}
                </span>
                {!historical && (
                  <Button disabled={disabled || !!busyItem} onClick={() => void restore(history, media)}>
                    恢复到此位置
                  </Button>
                )}
              </article>
            ))}
          </div>
        </Dialog>
      )}
    </div>
  );
}

function ImageSubject({
  item,
  label,
  primary,
  onRetry,
  children,
}: {
  item: ImageGroupItem;
  label: string;
  primary: boolean;
  onRetry?: () => void;
  children: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [item.url]);
  return (
    <article
      className={`image-subject${primary ? ' is-primary' : ''}`}
      tabIndex={item.status === 'ready' ? 0 : -1}
      aria-label={`${label}第 ${item.output_index} 张${primary ? '，主图' : ''}`}
    >
      <div className="image-subject-label">
        <span>{item.output_index}</span>
        {primary && (
          <span className="image-primary-badge">
            <Check size={12} />
            主图
          </span>
        )}
      </div>
      {item.status === 'ready' && item.url && !failed ? (
        <>
          <img
            src={item.url}
            alt={`${label}第 ${item.output_index} 张`}
            loading="lazy"
            onError={() => setFailed(true)}
          />
          {children}
        </>
      ) : (
        <div className="image-subject-state">
          {item.status === 'generating' ? (
            <>
              <Spin />
              <span>第 {item.output_index} 张生成中…</span>
            </>
          ) : (
            <>
              <ImageIcon size={24} />
              <span>
                {failed ? (onRetry ? '预览加载失败' : '预览加载失败，请刷新图片组') : item.error || '此位置尚无图片'}
              </span>
              {onRetry && (
                <Button
                  size="small"
                  aria-label={`重新读取第 ${item.output_index} 张图片`}
                  onClick={() => {
                    setFailed(false);
                    onRetry();
                  }}
                >
                  重新读取图片
                </Button>
              )}
            </>
          )}
        </div>
      )}
      {item.width && item.height && (
        <span className="image-subject-size">
          {item.width} × {item.height}
        </span>
      )}
    </article>
  );
}
