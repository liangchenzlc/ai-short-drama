import type { BatchItemView } from '@host/types/creation/workspace/workbenchView';
import type { BatchScene } from '@host/types/generations/batches/requests';
import { Button, Checkbox, Popover } from 'antd';
import { useState } from 'react';
import './batchToolbar.css';

export function BatchToolbar({ filteredIds, selectedIds, items, history, scenes, onSelect, onLaunch, onRetry, onViewBatch }: {
  filteredIds: string[];
  selectedIds: string[];
  items: BatchItemView[];
  history: { id: string; scene: BatchScene; items: BatchItemView[] }[];
  scenes: BatchScene[];
  onSelect: (ids: string[]) => void;
  onLaunch: (scene: BatchScene) => void;
  onRetry: () => void;
  onViewBatch: (id: string) => void;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
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
        <Popover trigger="click" title="批量记录" open={historyOpen} onOpenChange={setHistoryOpen} content={
          <div className="batchHistory" role="list" aria-label="批次记录">
            {history.flatMap((run) => Array.from({ length: Math.ceil(run.items.length / 100) }, (_, index) => {
              const entries = run.items.slice(index * 100, index * 100 + 100);
              const done = entries.filter((item) => item.status === 'succeeded').length;
              const errors = entries.filter((item) => item.status === 'failed').length;
              return <div role="listitem" key={run.id + ':' + index}>
                <button type="button" onClick={() => { onViewBatch(run.id); setHistoryOpen(false); }}>
                  <strong>{labels[run.scene]} · 批次 {index + 1} · {entries.length} 项</strong>
                  <span>完成 {done} · 失败 {errors}{entries.length > done + errors ? ' · 进行中' : ''}</span>
                </button>
              </div>;
            }))}
          </div>
        }>
          <Button type="text" size="small">批量记录</Button>
        </Popover>
      </div>}
    </div>
  );
}