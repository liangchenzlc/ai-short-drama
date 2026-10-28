import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { renderStageLabels as stages } from '@host/features/creation/assembly/export/presentation';
import { timecode } from '@host/features/creation/assembly/sessions/assembly-editing';
import { Dialog } from '@host/shared/ui/Dialog';
import { Button,Dropdown } from 'antd';
import { Download,Ellipsis,Play,RefreshCw,X } from 'lucide-react';

import type { AssemblyExportHistoryProps } from '@host/types/creation/assembly/export/dialogs';
export function AssemblyExportHistory({
  history,
  displayedHistory,
  moreHistory,
  busy,
  disabled,
  active,
  currentMediaId,
  feedback,
  onClose,
  onPlay,
  onApply,
  onRetry,
  onCancel,
  onRefresh,
  onMore,
  download,
}: AssemblyExportHistoryProps) {
  return (
    <Dialog
      title="导出记录"
      className="standard-creation-dialog assembly-operation-dialog assembly-history-dialog"
      onClose={onClose}
    >
      {feedback}
      <div className="assembly-dialog-body">
        {!history.length && <p>还没有导出记录</p>}
        <div className="assembly-history">
          {displayedHistory?.map((job) => (
            <article key={job.id}>
              <div className="assembly-history-meta">
                <strong>
                  {stages[job.stage] ??
                    {
                      succeeded: '已完成',
                      queued: '排队中',
                      running: '导出中',
                      failed: '导出失败',
                      cancelled: '已取消',
                    }[job.status]}
                </strong>
                <time>
                  {new Date(job.created_at.endsWith('Z') ? job.created_at : `${job.created_at}Z`).toLocaleString(
                    'zh-CN',
                    {
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                    },
                  )}
                </time>
                <span>
                  {job.resolution ? `${job.resolution} / ` : ''}
                  {job.duration_ms ? `${timecode(job.duration_ms)} / ` : ''}
                  {job.media_id === currentMediaId ? '当前成片' : job.is_stale ? '与当前草稿不同' : '当前草稿版本'}
                </span>
                {job.error && (
                  <details>
                    <summary>失败原因</summary>
                    <p role="alert">{job.error.message}</p>
                  </details>
                )}
              </div>
              <div className="assembly-inline-actions">
                {job.status === 'succeeded' && (
                  <>
                    <AssemblyAction label="播放" icon={<Play size={16} />} onClick={() => onPlay(job)} />
                    <AssemblyAction label="下载" icon={<Download size={16} />} href={download(job.id)} />
                    <Dropdown
                      trigger={['click']}
                      overlayClassName="standard-creation-popup"
                      menu={{
                        items: [
                          {
                            key: 'apply',
                            label: '设为当前成片',
                            disabled: disabled || job.media_id === currentMediaId,
                            onClick: () => void onApply(job),
                          },
                        ],
                      }}
                    >
                      <Button
                        type="text"
                        className="assembly-icon-action"
                        icon={<Ellipsis size={16} />}
                        aria-label="更多导出操作"
                        title="更多导出操作"
                      />
                    </Dropdown>
                  </>
                )}
                {['failed', 'cancelled'].includes(job.status) && (
                  <AssemblyAction
                    label="重试"
                    icon={<RefreshCw size={16} />}
                    disabled={disabled || job.can_retry === false || !!active}
                    onClick={() => void onRetry(job)}
                  />
                )}
                {['queued', 'running'].includes(job.status) && (
                  <>
                    <span>{job.progress}%</span>
                    <AssemblyAction
                      label="取消"
                      icon={<X size={16} />}
                      disabled={disabled || job.can_cancel === false || job.cancel_requested}
                      onClick={() => void onCancel(job)}
                    />
                  </>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>
      <div className="assembly-dialog-actions">
        <AssemblyAction
          label="刷新记录"
          icon={<RefreshCw size={16} />}
          disabled={busy}
          onClick={() => void onRefresh()}
        />
        {moreHistory && (
          <Button disabled={busy} onClick={() => void onMore()}>
            加载更早记录
          </Button>
        )}
      </div>
    </Dialog>
  );
}
