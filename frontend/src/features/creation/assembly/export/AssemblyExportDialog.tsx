import { assemblyOutputAspect } from '@host/features/creation/assembly/sessions/assembly-document';
import { timecode } from '@host/features/creation/assembly/sessions/assembly-editing';
import { Dialog } from '@host/shared/ui/Dialog';
import { Button, Checkbox, Select } from 'antd';
import { Upload } from 'lucide-react';
import { useState } from 'react';

import type { AssemblyExportDialogProps } from '@host/types/creation/assembly/export/dialogs';
export function AssemblyExportDialog({
  value,
  busy,
  disabled,
  includedCount,
  total,
  blocked,
  stale,
  feedback,
  onClose,
  onAspect,
  onResolution,
  onSubmit,
}: AssemblyExportDialogProps) {
  const [acknowledge, setAcknowledge] = useState(false);
  return (
    <Dialog
      title="导出本集成片"
      className="standard-creation-dialog assembly-operation-dialog"
      canClose={!busy}
      onClose={onClose}
    >
      {feedback}
      <div className="assembly-dialog-body">
        <dl className="assembly-export-summary">
          <div>
            <dt>片段</dt>
            <dd>{includedCount} 段</dd>
          </div>
          <div>
            <dt>时长</dt>
            <dd>{timecode(total)}</dd>
          </div>
          <div>
            <dt>画幅</dt>
            <dd>
              {value?.assembly?.aspect === 'auto' ? `自动 · ${assemblyOutputAspect(value)}` : value?.assembly?.aspect}
            </dd>
          </div>
          <div>
            <dt>格式</dt>
            <dd>MP4</dd>
          </div>
        </dl>
        <label className="assembly-export-resolution">
          输出画幅
          <Select
            classNames={{ popup: { root: 'standard-creation-popup' } }}
            aria-label="输出画幅"
            value={value?.assembly?.aspect ?? 'auto'}
            disabled={disabled}
            options={[
              { value: 'auto', label: '自动（跟随视频）' },
              { value: '16:9', label: '16:9' },
              { value: '9:16', label: '9:16' },
            ]}
            onChange={onAspect}
          />
        </label>
        <label className="assembly-export-resolution">
          导出清晰度
          <Select
            classNames={{ popup: { root: 'standard-creation-popup' } }}
            aria-label="导出清晰度"
            value={value?.assembly?.resolution}
            disabled={disabled}
            options={[
              { value: '720p', label: '720p' },
              { value: '1080p', label: '1080p' },
            ]}
            onChange={onResolution}
          />
        </label>
        <p className="assembly-export-hint">
          {value?.assembly?.output_width && value.assembly.output_height
            ? `实际输出 ${value.assembly.output_width} × ${value.assembly.output_height}。`
            : '视频尺寸检测中。'}
          自动优先使用时间轴中的首个分镜视频，其次使用导入视频。声音和字幕使用已保存的编辑设置，导出期间可以继续剪辑。
        </p>
        {stale && (
          <Checkbox checked={acknowledge} onChange={(e) => setAcknowledge(e.target.checked)}>
            已核对，继续使用轨道中的旧版分镜视频
          </Checkbox>
        )}
      </div>
      <div className="assembly-dialog-actions">
        <Button onClick={onClose} disabled={busy}>
          返回编辑
        </Button>
        <Button
          type="primary"
          icon={<Upload size={16} />}
          loading={busy}
          disabled={disabled || blocked || (stale && !acknowledge)}
          onClick={() => void onSubmit(acknowledge)}
        >
          开始导出
        </Button>
      </div>
    </Dialog>
  );
}
