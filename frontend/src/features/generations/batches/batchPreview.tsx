import { Dialog } from '@host/shared/ui/Dialog';
import type { BatchScene } from '@host/types/generations/batches/requests';
import { Button, Radio, Select } from 'antd';
import { useState } from 'react';
import './batchPreview.css';

export function BatchPreview({ scene, selectedIds, eligibleIds, missingIds, outsideFilterCount, blockedCount, onClose, onConfirm }: {
  scene: BatchScene;
  selectedIds: string[];
  eligibleIds: string[];
  missingIds: string[];
  outsideFilterCount: number;
  blockedCount: number;
  onClose: () => void;
  onConfirm: (ids: string[]) => void;
}) {
  const [mode, setMode] = useState<'missing' | 'regenerate'>('missing');
  const targets = mode === 'missing' ? missingIds : eligibleIds;
  const label = scene === 'asset_image' ? '资产图片' : scene === 'shot_image' ? '分镜图' : '分镜视频';
  return (
    <Dialog title="批量生成" className="batchPreviewModal" onClose={onClose}>
      <div className="batchPreviewDialog">
        <div className="batchPreviewSummary"><strong>{selectedIds.length} 项</strong><span>{label}</span></div>
        <label>生成模型<Select aria-label="批量生成模型" value="demo" options={[{ value: 'demo', label: '演示模型' }]} /></label>
        <Radio.Group value={mode} onChange={(event) => setMode(event.target.value)}>
          <Radio value="missing">仅生成缺失项</Radio>
          <Radio value="regenerate">重新生成已有结果</Radio>
        </Radio.Group>
        <p>可执行 {targets.length} 项{mode === 'missing' && eligibleIds.length > missingIds.length ?
          ' · 跳过 ' + (eligibleIds.length - missingIds.length) + ' 项已有结果' : ''}</p>
        {blockedCount > 0 && <p>参考图片超限 {blockedCount} 项</p>}
        {outsideFilterCount > 0 && <p>筛选外 {outsideFilterCount} 项不会生成</p>}
        <div className="batchPreviewDialogActions"><Button onClick={onClose}>取消</Button>
          <Button type="primary" disabled={!targets.length} onClick={() => onConfirm(targets)}>确认生成</Button></div>
      </div>
    </Dialog>
  );
}
