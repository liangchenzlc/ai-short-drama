import { createRoot } from 'react-dom/client';
import { Button } from 'antd';
import { StudioProvider } from './StudioProvider';
import { Dialog } from './Dialog';
import { Icon } from './Icon';

type ConfirmationOptions = { title?: string; confirmText?: string; cancelText?: string; danger?: boolean };
let open = false;

/** Async, focus-trapped confirmation. Repeated clicks cannot submit a second action. */
export function confirmAction(message: string, options: ConfirmationOptions = {}): Promise<boolean> {
  if (open) return Promise.resolve(false);
  open = true;
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  const discard = /放弃|尚未保存|未保存/.test(message);
  const remove = /删除|移除/.test(message);
  const danger = options.danger ?? (discard || remove);
  return new Promise<boolean>(resolve => {
    let settled = false;
    const finish = (accepted: boolean) => {
      if (settled) return;
      settled = true;
      // Close the top-layer dialog and restore focus before continuing the action.
      queueMicrotask(() => {
        root.unmount(); host.remove(); open = false; resolve(accepted);
      });
    };
    root.render(<StudioProvider>
      <Dialog title={options.title ?? (discard ? '未保存的修改' : remove ? '确认移除' : '确认操作')}
        className="studio-confirm-dialog" onClose={() => finish(false)}>
        <div className={`studio-confirm-content${danger ? ' is-danger' : ''}`}>
          <span className="studio-confirm-icon"><Icon name="warning" size={22}/></span>
          <p>{message}</p>
        </div>
        <div className="dialog-actions">
          <Button autoFocus data-dialog-autofocus onClick={() => finish(false)}>{options.cancelText ?? '取消'}</Button>
          <Button type="primary" danger={danger} onClick={() => finish(true)}>
            {options.confirmText ?? (discard ? '放弃修改' : remove ? '确认移除' : '确认继续')}
          </Button>
        </div>
      </Dialog>
    </StudioProvider>);
  });
}
