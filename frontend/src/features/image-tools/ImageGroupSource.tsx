import { useEffect, useState } from 'react';
import { Button, Spin } from 'antd';
import { imageGroups, type ImageGroup, type ImageTargetKind } from '../../api/modules/image-groups';
import { ApiError, errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { ImagePreview } from '../../components/ui/ImagePreview';
import { StudioFeedback } from '../../components/ui/StudioFeedback';

/** 精确回查所选图片组或图位，浏览不会修改当前主图。 */
export function ImageGroupSource({ groupId, slotId, targetKind, targetId, onClose }: {
  groupId: string; slotId?: string | null; targetKind: ImageTargetKind; targetId: string; onClose: () => void;
}) {
  const [group, setGroup] = useState<ImageGroup | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [preview, setPreview] = useState<{ src: string; alt: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setGroup(null);
    if (!/^[1-9]\d{0,19}$/.test(groupId) || slotId && !/^[1-9]\d{0,19}$/.test(slotId)) {
      setError('图片来源身份无效，请从任务引用重新打开。'); setLoading(false); return;
    }
    imageGroups.detail(groupId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.target_kind !== targetKind || value.target_id !== targetId || slotId && !value.items.some(item => item.id === slotId)) {
        throw new ApiError('图片来源不属于当前主体，或图位已移除。', 'source_mismatch');
      }
      setGroup(value);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [groupId, slotId, targetKind, targetId, revision]);
  const items = group?.items.filter(item => !slotId || item.id === slotId) ?? [];
  return <Dialog title={slotId ? '引用图位来源' : '引用图片组来源'} className="image-item-history-dialog standard-creation-dialog" onClose={onClose}>
    <div className="image-item-history">
      {loading ? <div role="status"><Spin/>正在读取保存的图片来源…</div> : error ? <StudioFeedback type="error" title="读取图片来源" message={error} noticeKey={`image-group-source:${groupId}`} actions={<Button onClick={() => setRevision(old => old + 1)}>重新读取来源</Button>}/> : group ? <>
        <p>图片组 {group.id} · {group.is_current ? '当前图片组' : '历史图片组'} · 主图版本 {group.primary_version}</p>
        {items.length ? items.map(item => <article key={item.id} aria-label={`来源图位 ${item.output_index}`}>
          <strong>第 {item.output_index} 张 · v{item.item_version}{group.primary_index === item.output_index ? ' · 组内主图' : ''}</strong>
          {item.url ? <button type="button" className="image-history-preview" aria-label={`预览来源第 ${item.output_index} 张`} onClick={() => setPreview({ src: item.url!, alt: `来源第 ${item.output_index} 张` })}><img src={item.url} alt={`来源第 ${item.output_index} 张`}/></button>
            : <p>{item.status === 'generating' ? '正在生成' : item.status === 'failed' ? '此图位尚无成功图片' : '图片暂不可读取'}</p>}
          <span>{item.width && item.height ? `${item.width} × ${item.height}` : '尚无图片尺寸'}</span>
        </article>) : <p>此图片组还没有图位。</p>}
      </> : null}
    </div>
    <footer className="production-dialog-footer"><Button onClick={onClose}>关闭来源</Button></footer>
    {preview && <ImagePreview presentation="standard-creation" src={preview.src} alt={preview.alt} onClose={() => setPreview(null)}/>}
  </Dialog>;
}
