import type { BatchItemView } from '@host/types/creation/workspace/workbenchView';
import type { BatchScene } from '@host/types/generations/batches/requests';
import { Button, Checkbox } from 'antd';
import './batchToolbar.css';

export function BatchToolbar({ filteredIds, selectedIds, items, scenes, onSelect, onLaunch, onRetry }: {
  filteredIds: string[];
  selectedIds: string[];
  items: BatchItemView[];
  scenes: BatchScene[];
  onSelect: (ids: string[]) => void;
  onLaunch: (scene: BatchScene) => void;
  onRetry: () => void;
}) {
  const checked = filteredIds.length > 0 && filteredIds.every((id) => selectedIds.includes(id));
  const some = filteredIds.some((id) => selectedIds.includes(id));
  const finished = items.filter((item) => item.status === 'succeeded').length;
  const failed = items.filter((item) => item.status === 'failed').length;
  const pending = items.length - finished - failed;
  const labels: Record<BatchScene, string> = {
    asset_image: '批量生成图片', shot_image: '批量生成分镜图', shot_video: '批量生成视频',
  };

  return (
    <div className="batchPreviewToolbar">
      <Checkbox aria-label="全选筛选结果" checked={checked} indeterminate={some && !checked} disabled={!filteredIds.length}
        onChange={(event) => onSelect(event.target.checked ? [...new Set([...selectedIds, ...filteredIds])] :
          selectedIds.filter((id) => !filteredIds.includes(id)))}>全选筛选结果</Checkbox>
      <div className="batchPreviewActions">{scenes.map((scene) =>
        <Button key={scene} size="small" disabled={!selectedIds.length} onClick={() => onLaunch(scene)}>{labels[scene]}</Button>)}</div>
      {!!items.length && <div className="batchPreviewProgress" role="status">
        {pending ? '进行中 ' + pending + ' · ' : ''}<span>完成 {finished} · 失败 {failed}</span>
        {items.length > 100 && <span>{Math.ceil(items.length / 100)} 个批次</span>}
        {failed > 0 && <Button type="link" size="small" onClick={onRetry}>重试失败项</Button>}
      </div>}
    </div>
  );
}