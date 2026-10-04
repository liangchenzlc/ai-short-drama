import { useId, useState } from 'react';
import { Checkbox } from 'antd';
import type { ShotRead } from '../../api/modules/storyboard';

export function StoryboardShotCard({ shot, selected, disabled, batch, checked, onSelect, onCheck }: {
  shot: ShotRead;
  selected: boolean;
  disabled: boolean;
  batch: boolean;
  checked: boolean;
  onSelect: () => void;
  onCheck: (checked: boolean) => void;
}) {
  const descriptionId = useId();
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const imageUrl = shot.image?.url;
  const imageState = shot.image?.is_stale ? '需核对' : shot.image ? '已采用' : '待生成';
  const videoState = shot.video?.is_stale ? '需核对' : shot.video ? '已采用' : '待生成';
  const position = String(shot.position).padStart(2, '0');
  return <article className={`storyboard-item storyboard-shot-card${selected ? ' is-selected' : ''}`}>
    {batch && <Checkbox className="batch-item-select" aria-label={`批量选择分镜 ${shot.position}`} checked={checked} disabled={disabled} onChange={event => onCheck(event.target.checked)}/>}
    <button type="button" className="storyboard-summary" aria-label={`选择分镜 ${position}`} aria-describedby={descriptionId} aria-pressed={selected} disabled={disabled} onClick={onSelect}>
      <span className="storyboard-card-media">
        {imageUrl && failedUrl !== imageUrl
          ? <img src={imageUrl} alt={`分镜 ${position} 的当前采用图片`} loading="lazy" decoding="async" onError={() => setFailedUrl(imageUrl)}/>
          : <span className="storyboard-card-placeholder"><span>{imageUrl ? '图片暂时无法预览' : '尚无采用画面'}</span><small>{imageUrl ? '打开分镜图核对当前图片' : shot.script.trim() ? '选择镜头后制作分镜图' : '先编写本镜脚本'}</small></span>}
      </span>
      <span className="storyboard-card-copy" id={descriptionId}>
        <span className="storyboard-card-heading"><strong>分镜 {position}</strong><span className="shot-summary-duration">{shot.duration_ms / 1000} 秒</span></span>
        <span className="storyboard-summary-script">{shot.script || '空分镜，点击编写脚本'}</span>
        <span className="storyboard-card-states">
          <span className={`shot-summary-state${shot.image?.is_stale ? ' is-stale' : shot.image ? ' is-ready' : ''}`}>图片：{imageState}</span>
          <span className={`shot-summary-state${shot.video?.is_stale ? ' is-stale' : shot.video ? ' is-ready' : ''}`}>视频：{videoState}</span>
        </span>
      </span>
    </button>
  </article>;
}
