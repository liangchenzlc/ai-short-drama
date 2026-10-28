import { dateLabel, taskLabel } from '@host/features/generations/presentation/presentation';
import { Alert, Button, Empty, Spin } from 'antd';

import type { ExtractionHistoryProps } from '@host/types/creation/assets/extraction/views';
export function ExtractionHistory({
  historyError,
  historyLoading,
  tasks,
  busy,
  historyOffset,
  total,
  refreshTasks,
  showTask,
  moreHistory,
}: ExtractionHistoryProps) {
  return (
    <>
      {historyError && (
        <Alert type="error" message={historyError} action={<Button onClick={() => void refreshTasks()}>重试</Button>} />
      )}
      {historyLoading && !tasks.length && (
        <div role="status">
          <Spin />
          <p>正在读取提取记录…</p>
        </div>
      )}
      {!historyLoading && !tasks.length && !historyError && <Empty description="暂无提取记录" />}
      {tasks.map((item) => (
        <div className="extraction-history-row" key={item.generation_id}>
          <div>
            <strong>{dateLabel(item.created_at)}</strong>
            <p>
              {item.config?.name ?? '文本模型'} · {taskLabel(item)}
            </p>
          </div>
          <Button disabled={busy} onClick={() => void showTask(item.generation_id)}>
            查看
          </Button>
        </div>
      ))}
      {historyOffset < total && (
        <Button disabled={busy || historyLoading} onClick={() => void moreHistory()}>
          加载更多记录
        </Button>
      )}
    </>
  );
}
