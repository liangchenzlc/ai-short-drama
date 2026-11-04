import { ApiError, errorMessage, isCancelled } from '@host/api/http';
import { aiModelConfigs } from '@host/features/ai-config/editor/config-api';
import { useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type { AiConfig, ConfigDraft, ServiceType } from '@host/types/ai-config/editor/config-model';
import { useEffect, useRef, useState } from 'react';

const PAGE_SIZE = 20;

export function useModelSettings(serviceType: ServiceType) {
  const message = useStudioMessage();
  const [items, setItems] = useState<AiConfig[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formRevision, setFormRevision] = useState(0);
  const [editing, setEditing] = useState<AiConfig | null>(null);
  const [testing, setTesting] = useState<AiConfig | null>(null);
  const [deleting, setDeleting] = useState<AiConfig | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleteConflict, setDeleteConflict] = useState(false);
  const uncertainCreate = useRef<string | null>(null);
  const reload = () => setRevision(value => value + 1);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    aiModelConfigs.list(serviceType, offset, PAGE_SIZE, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (!result.items.length && offset > 0) {
        setOffset(Math.max(0, Math.ceil(result.total / PAGE_SIZE) - 1) * PAGE_SIZE);
        return;
      }
      setItems(result.items);
      setTotal(result.total);
      setLoading(false);
    }).catch(cause => {
      if (controller.signal.aborted || isCancelled(cause)) return;
      setError(errorMessage(cause));
      setLoading(false);
    });
    return () => controller.abort();
  }, [serviceType, offset, revision]);

  async function save(draft: ConfigDraft, makeDefault: boolean, key: string) {
    let saved: AiConfig;
    if (editing) {
      saved = await aiModelConfigs.update(editing, draft);
    } else {
      // 响应未知时先按原身份核对，不按名称猜测，也不生成新请求身份。
      const existing = uncertainCreate.current === key
        ? (await aiModelConfigs.list(serviceType, 0, 1, undefined, key)).items[0]
        : undefined;
      try {
        saved = existing ?? await aiModelConfigs.create(draft, key);
        uncertainCreate.current = null;
      } catch (cause) {
        if (!(cause instanceof ApiError) || !cause.status || cause.status >= 500) uncertainCreate.current = key;
        throw cause;
      }
    }
    if (makeDefault && !saved.isDefault) {
      try {
        saved = await aiModelConfigs.setDefault(saved.id, saved.rowVersion);
      } catch (cause) {
        setEditing(saved);
        reload();
        throw new ApiError(`模型已保存，设为默认未完成：${errorMessage(cause)}`, 'DEFAULT_UPDATE_FAILED');
      }
    }
    setFormOpen(false);
    setEditing(null);
    reload();
  }

  async function edit(item: AiConfig) {
    setBusyId(item.id);
    try {
      setEditing(await aiModelConfigs.get(item.id));
      setFormOpen(true);
    } catch (cause) { message.error(errorMessage(cause)); }
    finally { setBusyId(null); }
  }

  async function change(item: AiConfig, action: 'default' | 'enabled') {
    setBusyId(item.id);
    try {
      if (action === 'default') await aiModelConfigs.setDefault(item.id, item.rowVersion);
      else await aiModelConfigs.setEnabled(item.id, item.rowVersion, !item.enabled);
      reload();
    } catch (cause) { message.error(errorMessage(cause)); }
    finally { setBusyId(null); }
  }

  async function remove() {
    if (!deleting) return;
    setBusyId(deleting.id);
    setDeleteError('');
    try {
      await aiModelConfigs.remove(deleting.id, deleting.rowVersion);
      setDeleting(null);
      reload();
    } catch (cause) {
      setDeleteError(errorMessage(cause));
      setDeleteConflict(cause instanceof ApiError && [404, 409].includes(cause.status ?? 0));
    } finally { setBusyId(null); }
  }

  return {
    items, total, offset, pageSize: PAGE_SIZE, loading, error, busyId, reload,
    formOpen, formRevision, editing, testing, deleting, deleteError, deleteConflict,
    setOffset, setTesting, setDeleting, save, edit, remove,
    setDefault: (item: AiConfig) => change(item, 'default'),
    toggleEnabled: (item: AiConfig) => change(item, 'enabled'),
    add() { setEditing(null); setFormOpen(true); },
    closeForm() { setFormOpen(false); setEditing(null); },
    confirmDelete(item: AiConfig) { setDeleting(item); setDeleteError(''); setDeleteConflict(false); },
    async reloadEditing() {
      if (!editing) return;
      setEditing(await aiModelConfigs.get(editing.id));
      setFormRevision(value => value + 1);
      reload();
    },
  };
}
