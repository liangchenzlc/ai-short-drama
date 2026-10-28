import { Dialog } from '@host/shared/ui/Dialog';
import { Button } from 'antd';

import type { AssemblyAdoptionDialogProps } from '@host/types/creation/assembly/export/dialogs';
export function AssemblyAdoptionDialog({ applying, busy, feedback, onClose, onSubmit }: AssemblyAdoptionDialogProps) {
  return (
    <Dialog
      title="设为当前成片"
      className="standard-creation-dialog assembly-operation-dialog"
      canClose={!busy}
      onClose={onClose}
    >
      {feedback}
      <div className="assembly-dialog-body">
        <p>将采用这次导出的结果作为本集当前成片。其他导出记录仍会保留。</p>
        {applying.is_stale && <p>此结果与当前草稿不同，请确认画面和顺序。</p>}
      </div>
      <div className="assembly-dialog-actions">
        <Button disabled={busy} onClick={onClose}>
          取消
        </Button>
        <Button type="primary" loading={busy} onClick={() => void onSubmit()}>
          确认采用
        </Button>
      </div>
    </Dialog>
  );
}
