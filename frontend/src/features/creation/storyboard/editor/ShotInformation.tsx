import { StudioFeedback as Alert } from '@host/shared/ui/StudioFeedback';
import type { ShotInformationProps } from '@host/types/creation/storyboard/editor/information';
import { Button, Input, InputNumber, Select } from 'antd';
export function ShotInformation({
  shot,
  disabled,
  reloading,
  hasPrevious,
  status,
  onChange,
  onDownloadRecovery,
  onRetry,
  onReload,
}: ShotInformationProps) {
  const details = shot.details ?? {};
  const setDetail = (key: keyof typeof details, value: string) => onChange({ details: { ...details, [key]: value } });
  return (
    <div className="storyboard-shot-information">
      <label className="writing-control">
        <span>镜头名称</span>
        <Input
          aria-label="镜头名称"
          maxLength={255}
          value={shot.title ?? ''}
          disabled={disabled}
          onChange={(event) => onChange({ title: event.target.value })}
        />
      </label>
      <div className="storyboardFieldRow">
        <label>景别<Select aria-label="景别" value={shot.shot_size ?? undefined} disabled={disabled} allowClear
          onChange={(shot_size) => onChange({ shot_size: shot_size ?? null })}
          options={[
            { value: 'wide_shot', label: '远景' }, { value: 'full_shot', label: '全景' },
            { value: 'medium_shot', label: '中景' }, { value: 'close_up', label: '特写' },
            { value: 'extreme_close_up', label: '大特写' }, { value: 'two_shot', label: '双人镜头' },
          ]} /></label>
        <label>时长（秒）<InputNumber aria-label="时长（秒）" min={1} max={10} value={shot.duration_ms / 1000}
          disabled={disabled} onChange={(value) => onChange({ duration_ms: (value ?? 1) * 1000 })} /></label>
      </div>
      <div className="storyboardFieldRow">
        <label>机位角度<Input aria-label="机位角度" value={details.camera_angle ?? ''} disabled={disabled}
          onChange={(event) => setDetail('camera_angle', event.target.value)} /></label>
        <label>运镜<Input aria-label="运镜" value={details.camera_movement ?? ''} disabled={disabled}
          onChange={(event) => setDetail('camera_movement', event.target.value)} /></label>
      </div>
      <label className="writing-control">
        <span>镜头脚本</span>
        <Input.TextArea
          aria-label={'分镜 ' + shot.position + ' 脚本'}
          rows={4}
          value={shot.script}
          disabled={disabled}
          onChange={(event) => onChange({ script: event.target.value })}
        />
      </label>
      <div className="storyboardFieldRow">
        <label>对白设想<Input.TextArea aria-label="对白设想" autoSize={{ minRows: 1, maxRows: 3 }}
          value={details.dialogue_note ?? ''} disabled={disabled} onChange={(event) => setDetail('dialogue_note', event.target.value)} /></label>
        <label>声音<Input aria-label="声音" value={details.sound ?? ''} disabled={disabled}
          onChange={(event) => setDetail('sound', event.target.value)} /></label>
      </div>
      {hasPrevious && (
        <Button type="text" onClick={() => onDownloadRecovery()}>
          下载本机备份
        </Button>
      )}
      {['error', 'conflict'].includes(status ?? '') && (
        <Alert
          className="storyboard-shot-feedback"
          type="warning"
          message="镜头保存已暂停，输入保留。"
          action={
            <>
              <Button
                onClick={() => {
                  const url = URL.createObjectURL(
                    new Blob([JSON.stringify(shot, null, 2)], {
                      type: 'application/json',
                    }),
                  );
                  const link = document.createElement('a');
                  link.href = url;
                  link.download = `shot-${shot.id}-draft.json`;
                  link.click();
                  URL.revokeObjectURL(url);
                }}
              >
                下载镜头草稿
              </Button>
              {status === 'error' && <Button onClick={() => void onRetry()}>重试保存镜头</Button>}
              {status === 'conflict' && (
                <Button loading={reloading} onClick={() => void onReload(true)}>
                  以当前版本保留本机修改
                </Button>
              )}
              <Button loading={reloading} onClick={() => void onReload()}>
                载入服务端镜头
              </Button>
            </>
          }
        />
      )}
    </div>
  );
}
