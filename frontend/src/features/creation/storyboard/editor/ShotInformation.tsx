import { StudioFeedback as Alert } from '@host/shared/ui/StudioFeedback';
import type { ShotInformationProps } from '@host/types/creation/storyboard/editor/information';
import { Button, Input } from 'antd';
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
