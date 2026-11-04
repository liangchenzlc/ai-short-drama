import { memoryDirty, memoryUpdatedLabel, memoryValidation } from '@host/features/assistant/settings/memory-manager-state';
import { useMemoryManager } from '@host/features/assistant/settings/useMemoryManager';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { MemoryManagerController, MemoryManagerProps } from '@host/types/assistant/settings/memory';
import { Button, Empty, Input, Pagination, Skeleton, Spin, Tooltip } from 'antd';
import { ArrowLeft, Pencil, Search, Trash2 } from 'lucide-react';
import './memory-manager.css';

function MemoryEditor({ state }: { state: MemoryManagerController }) {
  const edit = state.selected!;
  const validation = memoryValidation(edit.value);
  return <div className="memory-manager-editor">
    <Button type="text" className="memory-manager-back" icon={<ArrowLeft size={14} />} onClick={state.back}>返回记忆</Button>
    {edit.issue && <StudioFeedback persistent type="error" noticeKey={`memory-edit-${edit.saved.id}`} message={edit.issue}
      actions={!edit.conflict && <Button loading={state.saving} onClick={() => void state.retry()}>重新核对</Button>} />}
    <label><span>标题</span><Input autoFocus aria-label="记忆标题" maxLength={200} value={edit.value.title}
      onChange={event => state.edit('title', event.target.value)} /></label>
    <label><span>内容</span><Input.TextArea aria-label="记忆内容" maxLength={12000} value={edit.value.content}
      onChange={event => state.edit('content', event.target.value)} /></label>
    <div className="memory-manager-editor-meta"><span>{validation && <span role="alert">{validation}</span>}{state.saving && <span role="status" aria-label="正在同步记忆"><Spin size="small" /></span>}</span><span>{edit.value.content.length} / 12000</span></div>
    {edit.conflict && <section className="memory-manager-conflict" aria-label="核对记忆变更">
      <p>{edit.issue}</p>
      <div><section><strong>我的输入</strong><h3>{edit.value.title}</h3><p>{edit.value.content}</p><Button onClick={() => state.resolve(false)}>保留我的输入</Button></section>
        <section><strong>服务端内容</strong><h3>{edit.conflict.title}</h3><p>{edit.conflict.content}</p><Button onClick={() => state.resolve(true)}>使用服务端内容</Button></section></div>
    </section>}
  </div>;
}

function MemoryList({ state }: { state: MemoryManagerController }) {
  return <>
    <div className="memory-manager-search"><Input autoFocus aria-label="搜索记忆" placeholder="搜索记忆" maxLength={120} prefix={<Search size={15} />}
      allowClear value={state.query} onChange={event => state.setQuery(event.target.value)} /></div>
    <div className="memory-manager-list" aria-busy={state.loading}>
      {state.loading ? <Skeleton active paragraph={{ rows: 6 }} /> : state.listIssue ?
        <div className="memory-manager-empty" role="alert"><p>记忆读取失败</p><p>{state.listIssue}</p><Button onClick={() => void state.read()}>重新读取</Button></div> :
        !state.page.items.length ? <Empty className="memory-manager-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description={state.query.trim() ? '没有找到匹配的记忆' : '还没有长期记忆'} /> :
          state.page.items.map(item => <article className="memory-manager-item" key={item.id}>
            <div><h3>{item.title}</h3><div className="memory-manager-actions">
              <Tooltip title="编辑"><Button type="text" aria-label={`编辑记忆：${item.title}`} icon={<Pencil size={15} />} onClick={() => state.select(item)} /></Tooltip>
              <Tooltip title="删除"><Button type="text" disabled={state.saving} aria-label={`删除记忆：${item.title}`} icon={<Trash2 size={15} />} onClick={() => state.requestDelete(item)} /></Tooltip>
            </div></div><p>{item.content}</p><time dateTime={item.updated_at}>{memoryUpdatedLabel(item.updated_at)}</time>
          </article>)}
    </div>
    <footer className="memory-manager-footer"><span>共 {state.page.total} 条记忆</span>
      <Pagination simple size="small" current={Math.floor(state.offset / state.page.limit) + 1} pageSize={state.page.limit}
        total={state.page.total} hideOnSinglePage showSizeChanger={false} onChange={value => state.setOffset((value - 1) * state.page.limit)} />
    </footer>
  </>;
}

export function MemoryManager({ open, onClose }: MemoryManagerProps) {
  const state = useMemoryManager(open);
  const deleteBlocked = state.selected && memoryDirty(state.selected);
  return <>
    <Dialog title={state.selected ? '编辑记忆' : '管理记忆'} open={open} onClose={onClose} className="memory-manager-drawer">
      {state.selected ? <MemoryEditor state={state} /> : <MemoryList state={state} />}
    </Dialog>
    {state.deleting && <Dialog title="删除记忆" open={open} onClose={state.cancelDelete} canClose={!state.deletingBusy} className="memory-delete-dialog">
      {state.deleting.issue && <StudioFeedback persistent type="warning" noticeKey="memory-delete-problem" message={state.deleting.issue} />}
      <p>删除「{state.deleting.item.title}」后，助手将不再使用这条记忆。</p>
      <p className="memory-delete-content">{state.deleting.item.content}</p>
      <div className="dialog-actions"><Button disabled={state.deletingBusy} onClick={state.cancelDelete}>取消</Button>
        <Button danger loading={state.deletingBusy} disabled={!!deleteBlocked} onClick={() => void state.confirmDelete()}>{state.deleting.unknown ? '核对删除结果' : '删除'}</Button></div>
    </Dialog>}
  </>;
}
