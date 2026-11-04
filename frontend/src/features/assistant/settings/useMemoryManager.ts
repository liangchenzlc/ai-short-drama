import { memoryApi } from '@host/api/assistant/settings/memory';
import { ApiError, errorMessage } from '@host/api/http';
import { memoryDirty, memoryEditor, memoryFields, memoryValidation, receiveMemory, sameMemory } from '@host/features/assistant/settings/memory-manager-state';
import type { MemoryDelete, MemoryEdit, MemoryItem, MemoryManagerController, MemoryPage } from '@host/types/assistant/settings/memory';
import { useCallback, useEffect, useRef, useState } from 'react';

const pageSize = 20;
export function useMemoryManager(open: boolean): MemoryManagerController {
  const [page, setPage] = useState<MemoryPage>({ items: [], total: 0, offset: 0, limit: pageSize });
  const [query, setQueryValue] = useState('');
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listIssue, setListIssue] = useState('');
  const [drafts, setDrafts] = useState<Record<string, MemoryEdit>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<MemoryDelete | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);
  const latest = useRef(drafts);
  const live = useRef(true), lock = useRef(false), listRequest = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const update = useCallback((id: string, change: (old: MemoryEdit) => MemoryEdit) => {
    const next = { ...latest.current, [id]: change(latest.current[id]) };
    latest.current = next; setDrafts(next);
  }, []);
  const read = useCallback(async () => {
    const request = ++listRequest.current;
    setLoading(true); setListIssue('');
    try {
      const value = await memoryApi.list({ query: query.trim() || undefined, offset, limit: pageSize });
      if (live.current && request === listRequest.current) setPage(value);
    } catch (cause) {
      if (live.current && request === listRequest.current) setListIssue(errorMessage(cause));
    } finally {
      if (live.current && request === listRequest.current) setLoading(false);
    }
  }, [query, offset]);
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => void read(), 250);
    return () => { window.clearTimeout(timer); ++listRequest.current; };
  }, [open, read]);

  const acknowledge = useCallback((id: string, item: MemoryItem) => {
    const submission = latest.current[id].submission!;
    update(id, old => receiveMemory(old, item, submission));
    setPage(old => ({ ...old, items: old.items.map(row => row.id === id ? item : row) }));
  }, [update]);
  const reconcile = useCallback(async (id: string, cause?: unknown) => {
    try {
      const item = await memoryApi.get(id);
      if (!live.current) return;
      const edit = latest.current[id];
      if (edit.submission && sameMemory(item, edit.submission.patch)) {
        acknowledge(id, item);
      } else if (item.state !== 'active') {
        update(id, old => ({ ...old, blocked: true, issue: '这条记忆已删除，当前输入仍保留。', conflict: null, submission: null }));
      } else if (item.content_version !== edit.saved.content_version) {
        update(id, old => ({ ...old, blocked: true, conflict: item, issue: '这条记忆已在其他位置修改，请选择要保留的内容。', submission: null }));
      } else {
        update(id, old => ({ ...old, blocked: !!cause, submission: null, issue: cause ? `未能同步，输入已保留：${errorMessage(cause)}` : '' }));
      }
    } catch (cause) {
      if (live.current) update(id, old => ({ ...old, blocked: true, issue: `未能核对同步结果，输入已保留：${errorMessage(cause)}` }));
    }
  }, [acknowledge, update]);
  const flush = useCallback(async (id: string) => {
    if (lock.current) return;
    const edit = latest.current[id];
    const submission = { patch: { content_version: edit.saved.content_version, ...edit.value }, revision: edit.revision };
    lock.current = true; setSaving(true);
    update(id, old => ({ ...old, submission, issue: '' }));
    try {
      const item = await memoryApi.patch(id, submission.patch);
      if (live.current) acknowledge(id, item);
    } catch (cause) {
      if (live.current) {
        update(id, old => ({ ...old, blocked: true }));
        // 断连可能发生在提交之后，先按原字段核对结果，再允许重新写入。
        await reconcile(id, cause);
      }
    } finally { lock.current = false; if (live.current) setSaving(false); }
  }, [acknowledge, reconcile, update]);
  useEffect(() => {
    if (saving || deletingBusy) return;
    const pending = Object.entries(drafts).find(([, edit]) => memoryDirty(edit) && !edit.blocked && !memoryValidation(edit.value));
    if (!pending) return;
    const timer = window.setTimeout(() => void flush(pending[0]), 600);
    return () => window.clearTimeout(timer);
  }, [drafts, saving, deletingBusy, flush]);
  const unsynced = saving || deleting?.unknown || Object.values(drafts).some(edit => memoryDirty(edit) || !!edit.submission);
  useEffect(() => {
    if (!unsynced) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [unsynced]);

  const finishDelete = useCallback(async (id: string) => {
    const next = { ...latest.current }; delete next[id]; latest.current = next; setDrafts(next);
    setSelectedId(old => old === id ? null : old); setDeleting(null);
    if (offset && page.items.length === 1) setOffset(Math.max(0, offset - pageSize));
    else await read();
  }, [offset, page.items.length, read]);
  async function confirmDelete() {
    if (!deleting || lock.current) return;
    lock.current = true; setDeletingBusy(true);
    const { item, unknown } = deleting;
    try {
      if (unknown) {
        const current = await memoryApi.get(item.id);
        if (!live.current) return;
        if (current.state === 'forgotten') await finishDelete(item.id);
        else setDeleting({ item: current, unknown: false, issue: '记忆仍在，请核对内容后再次确认删除。' });
      } else {
        await memoryApi.remove(item.id, item.content_version);
        if (live.current) await finishDelete(item.id);
      }
    } catch (cause) {
      if (!live.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try {
          const current = await memoryApi.get(item.id);
          if (live.current) setDeleting({ item: current, unknown: false, issue: '内容已更新，请核对后再次确认删除。' });
        } catch { setDeleting({ item, unknown: true, issue: '未能读取最新内容，请核对删除结果。' }); }
      } else setDeleting({ item, unknown: true, issue: `未能确认删除结果：${errorMessage(cause)}` });
    } finally { lock.current = false; if (live.current) setDeletingBusy(false); }
  }
  const selected = selectedId ? drafts[selectedId] ?? null : null;
  return { page, loading, listIssue, query, offset, selected, saving, deleting, deletingBusy, read, setOffset,
    setQuery(value) { setQueryValue(value); setOffset(0); },
    select(item) {
      const existing = latest.current[item.id];
      if (!existing || !memoryDirty(existing) && !existing.blocked) {
        const next = { ...latest.current, [item.id]: memoryEditor(item) }; latest.current = next; setDrafts(next);
      }
      setSelectedId(item.id);
    },
    back() { setSelectedId(null); void read(); },
    edit(field, value) { if (selectedId) update(selectedId, old => ({ ...old, value: { ...old.value, [field]: value }, revision: old.revision + 1 })); },
    async retry() {
      if (!selectedId || lock.current) return;
      lock.current = true; setSaving(true);
      try { await reconcile(selectedId); }
      finally { lock.current = false; if (live.current) setSaving(false); }
    },
    resolve(useServer) {
      if (!selectedId || !selected?.conflict) return;
      const item = selected.conflict;
      update(selectedId, old => ({ ...old, saved: item, value: useServer ? memoryFields(item) : old.value,
        blocked: false, conflict: null, issue: '', submission: null, revision: old.revision + 1 }));
    },
    requestDelete(item) { setDeleting({ item: latest.current[item.id]?.saved ?? item, unknown: false, issue: '' }); },
    cancelDelete() { if (!deletingBusy) setDeleting(null); },
    confirmDelete,
  };
}
