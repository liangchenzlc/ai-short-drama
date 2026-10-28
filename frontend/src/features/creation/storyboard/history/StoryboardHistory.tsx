import { StoryboardResultPreview } from '@host/features/creation/storyboard/history/StoryboardResultPreview';
import { taskLabel } from '@host/features/generations/presentation/presentation';
import { Dialog } from '@host/shared/ui/Dialog';
import { LazyLoadMore } from '@host/shared/ui/LazyLoadMore';
import { StudioFeedback as Alert } from '@host/shared/ui/StudioFeedback';
import type { StoryboardHistoryProps } from '@host/types/creation/storyboard/history/views';
import { Button } from 'antd';

export function StoryboardHistory({
  projectId,
  busy,
  candidate,
  setCandidate,
  readOnly,
  loading,
  loadError,
  hasPage,
  message,
  setMessage,
  applyResult,
  tasksLoading,
  tasks,
  tasksError,
  setTaskRevision,
  tasksLoaded,
  setTaskDetailId,
  tasksTotal,
  loadMoreTasks,
  renderShotActions,
  setActionsOrigin,
  setHistoryOpen
}: StoryboardHistoryProps) {
  return (<Dialog
    title="分镜生成记录"
    className="storyboard-history-dialog standard-creation-dialog"
    canClose={!busy}
    onClose={() => {
      setHistoryOpen(false);
      setCandidate(null);
    }}
  >
    {candidate ? (
      <>
        <Button type="link" onClick={() => setCandidate(null)}>
          返回生成记录
        </Button>
        <StoryboardResultPreview
          key={candidate.generation_id}
          projectId={projectId}
          generationId={candidate.generation_id}
          busy={busy}
          disabled={readOnly || loading || !!loadError || !hasPage}
          error={message}
          onApply={(mode) => void applyResult(mode)}
        />
      </>
    ) : (
      <div className="lazy-scroll storyboard-history-scroll">
        {tasksLoading && !tasks.length && (
          <p role="status" className="creation-help">
            正在加载分镜生成记录…
          </p>
        )}
        {tasksError && (
          <Alert
            type="error"
            showIcon
            message={'分镜生成记录加载失败：' + tasksError}
            action={
              <Button loading={tasksLoading} onClick={() => setTaskRevision((revision) => revision + 1)}>
                重新加载生成记录
              </Button>
            }
          />
        )}
        {tasksLoaded && !tasksLoading && !tasksError && !tasks.length && (
          <p className="creation-help">暂无分镜生成记录，编写或采用剧本后开始生成。</p>
        )}
        {tasks.map((task) => (
          <div key={task.generation_id} className="resource-import-row">
            <div>
              <strong>{taskLabel(task)}</strong>
              <p>{new Date(task.created_at).toLocaleString('zh-CN')}</p>
              <small>任务 {task.generation_id}</small>
              {task.error && <p role="alert">{task.error.message}</p>}
            </div>
            {task.status === 'succeeded' ? (
              <Button
                onClick={() => {
                  setMessage('');
                  setCandidate(task);
                }}
              >
                查看分镜
              </Button>
            ) : (
              <Button onClick={() => setTaskDetailId(task.generation_id)}>查看任务</Button>
            )}
          </div>
        ))}
        <LazyLoadMore
          hasMore={tasks.length < tasksTotal}
          loading={tasksLoading}
          error={tasksError}
          onLoad={() => void loadMoreTasks()}
        />
      </div>
    )}
    <div className="production-dialog-footer">
      {renderShotActions('history')}
      <Button
        disabled={busy}
        onClick={() => {
          setActionsOrigin(null);
          setHistoryOpen(false);
          setCandidate(null);
        }}
      >
        关闭
      </Button>
    </div>
  </Dialog>);
}
