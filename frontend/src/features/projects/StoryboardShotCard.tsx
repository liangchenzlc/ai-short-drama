import { useId, useState } from 'react';
import { Checkbox } from 'antd';
import { Check } from 'lucide-react';
import type { ShotRead } from '../../api/modules/storyboard';

export function StoryboardShotCard({ shot, selected, disabled, batch, checked, onSelect, onCheck, media = 'image' }: {
  shot: ShotRead;
  selected: boolean;
  disabled: boolean;
  batch: boolean;
  checked: boolean;
  onSelect: () => void;
  onCheck: (checked: boolean) => void;
  media?: 'image' | 'video';
}) {
  const descriptionId = useId();
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const imageUrl = shot.image?.url;
  const current = media === 'video' ? shot.video : shot.image;
  const size = { full_shot: '全景', medium_shot: '中景', close_up: '近景', extreme_close_up: '特写', wide_shot: '远景', two_shot: '双人镜头' };
  const position = String(shot.position).padStart(2, '0');
  return <article id={`storyboard-shot-${shot.id}`} className={`storyboard-item storyboard-shot-card${selected ? ' is-selected' : ''}`}>
    {batch && <Checkbox className="batch-item-select" aria-label={`批量选择分镜 ${shot.position}`} checked={checked} disabled={disabled} onChange={event => onCheck(event.target.checked)}/>}
    <button type="button" className="storyboard-summary" aria-label={`选择分镜 ${position}`} aria-describedby={descriptionId} aria-pressed={selected} disabled={disabled} onClick={onSelect}>
      <span className="storyboard-card-media">
        <span className="standard-shot-number">{position} · {shot.duration_ms / 1000} 秒</span>
        {!current && <span className="standard-shot-pending">待制作</span>}
        {selected && <span className="standard-shot-selection" aria-label="当前选中"><Check size={14}/></span>}
        {imageUrl && failedUrl !== imageUrl
          ? <img src={imageUrl} alt={`分镜 ${position} 的当前采用图片`} loading="lazy" decoding="async" onError={() => setFailedUrl(imageUrl)}/>
          : <span className="storyboard-card-placeholder"><span>{imageUrl ? '图片暂时无法预览' : '尚无采用画面'}</span><small>{imageUrl ? '打开分镜图核对当前图片' : shot.script.trim() ? '选择镜头后制作分镜图' : '先编写本镜脚本'}</small></span>}
      </span>
      <span className="storyboard-card-copy" id={descriptionId}>
        <span className="storyboard-card-heading"><strong title={shot.title || '未命名镜头'}>{shot.title || '未命名镜头'}</strong><span>{shot.shot_size ? size[shot.shot_size] : ''}</span></span>
      </span>
    </button>
  </article>;
}
