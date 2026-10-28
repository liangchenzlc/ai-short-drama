import { Dialog } from '@host/shared/ui/Dialog';
import { Button } from 'antd';

import type { AssemblySyncDialogProps } from '@host/types/creation/assembly/export/dialogs';
export function AssemblySyncDialog({ changes, busy, feedback, onClose, onSubmit }: AssemblySyncDialogProps) {
  return (
    <Dialog
      title="同步分镜视频"
      className="standard-creation-dialog assembly-operation-dialog"
      canClose={!busy}
      onClose={onClose}
    >
      {feedback}
      <div className="assembly-dialog-body">
        <p>保留现有顺序与裁剪。替换视频会更新该镜头的所有片段，视频变短时需重新裁剪。</p>
        {changes?.length ? (
          <div className="assembly-sync-list">
            {changes.map((c) => (
              <div key={c.shot_id}>
                <strong>镜头 {String(c.position).padStart(2, '0')}</strong>
                <span>
                  {
                    {
                      added: '追加到末尾',
                      replacement: '替换来源视频',
                      archived: '移出导出范围',
                    }[c.kind]
                  }
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p>没有视频变化，可重新检测未就绪的视频。</p>
        )}
      </div>
      <div className="assembly-dialog-actions">
        <Button disabled={busy} onClick={onClose}>
          取消
        </Button>
        <Button type="primary" loading={busy} onClick={() => void onSubmit()}>
          确认同步
        </Button>
      </div>
    </Dialog>
  );
}
