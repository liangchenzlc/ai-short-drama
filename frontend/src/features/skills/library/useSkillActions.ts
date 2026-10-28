import { ApiError, errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { workflowStorage } from '@host/features/identity/storage/account-storage';
import { readSkillAction, saveSkillAction } from '@host/features/skills/imports/skill-draft';
import { modeChoice } from '@host/features/skills/library/skill-model';
import { confirmAction } from '@host/shared/ui/confirm';
import type { AgentSkillDetail } from '@host/types/assistant/engine';
import type { SkillPendingAction } from '@host/types/skills/imports/skill-draft';
import { useEffect, useRef, useState } from 'react';
export function useSkillActions(
  accountId: string,
  onComplete: (action: SkillPendingAction, detail?: AgentSkillDetail) => void,
) {
  const [storage] = useState(workflowStorage);
  const [restored] = useState(() => readSkillAction(accountId, storage));
  const [pending, setPending] = useState(restored.action);
  const [storageError, setStorageError] = useState(restored.error);
  const [issue, setIssue] = useState(restored.action ? '操作结果待核对' : restored.error);
  const [open, setOpen] = useState(!!restored.action || !!restored.error);
  const [latest, setLatest] = useState<AgentSkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const live = useRef(true);
  const completed = useRef(onComplete);
  completed.current = onComplete;
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  function remember(action: SkillPendingAction | null) {
    if (!saveSkillAction(accountId, action, storage)) {
      setIssue('本机记录未能保存，请恢复浏览器存储后重试');
      setOpen(true);
      return false;
    }
    setPending(action);
    setLatest(null);
    return true;
  }
  function finish(action: SkillPendingAction, detail?: AgentSkillDetail) {
    if (!remember(null)) return;
    setOpen(false);
    setIssue('');
    completed.current(action, detail);
  }
  async function execute(action: SkillPendingAction, reconcile = false) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setIssue('');
    try {
      if (reconcile && action.kind !== 'copy') {
        let server: AgentSkillDetail;
        try {
          server = await skillsApi.skill(action.item.id);
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 404 && action.kind === 'delete') {
            if (live.current) finish(action);
            return;
          }
          throw cause;
        }
        if (!live.current) return;
        if (
          (action.kind === 'settings' && server.enabled === action.enabled) ||
          (action.kind === 'mode' && modeChoice(server.modes) === modeChoice(action.modes))
        ) {
          finish(action, server);
          return;
        }
        if (
          action.kind === 'settings'
            ? server.settings_version !== action.item.settings_version
            : server.row_version !== action.item.row_version
        ) {
          setLatest(server);
          setIssue('版本已变化，请核对当前状态');
          return;
        }
      }
      const detail =
        action.kind === 'copy'
          ? await skillsApi.copySkill(action.item.id, action.item.content_version, action.key)
          : action.kind === 'settings'
            ? await skillsApi.updateSkillSettings(action.item.id, {
                row_version: action.item.settings_version,
                enabled: action.enabled,
              })
            : action.kind === 'mode'
              ? await skillsApi.updateSkill(action.item.id, {
                  row_version: action.item.row_version!,
                  modes: action.modes,
                })
              : await skillsApi.deleteSkill(action.item.id, action.item.row_version!);
      if (live.current) finish(action, detail || undefined);
    } catch (cause) {
      if (!live.current) return;
      const unknown = !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;
      const conflict = cause instanceof ApiError && cause.status === 409;
      if ((!unknown && !conflict) || (conflict && action.kind === 'copy')) remember(null);
      setIssue(
        unknown ? '操作结果待核对，原请求已保留' : conflict ? '版本已变化，请核对当前状态' : errorMessage(cause),
      );
      setOpen(true);
    } finally {
      lock.current = false;
      if (live.current) setBusy(false);
    }
  }
  function start(action: SkillPendingAction) {
    if (lock.current || pending || storageError) return;
    if (remember(action)) void execute(action);
  }
  async function reapply() {
    if (
      !pending ||
      !latest ||
      busy ||
      !(await confirmAction(`已核对「${latest.name}」的当前状态，按此版本继续？`, {
        title: '核对 Skill 状态',
        confirmText: '按此版本继续',
        danger: pending.kind === 'delete',
      }))
    )
      return;
    const action = { ...pending, item: latest };
    if (remember(action)) void execute(action);
  }
  async function discard() {
    if (
      busy ||
      !(await confirmAction('请先下载需要保留的记录。清除后将不再恢复此操作。', {
        title: '清除操作记录',
        confirmText: '明确清除',
      }))
    )
      return;
    if (remember(null)) {
      setStorageError('');
      setIssue('');
      setOpen(false);
    }
  }
  return {
    pending,
    issue,
    open,
    setOpen,
    latest,
    busy,
    storageError,
    start,
    reconcile: () => pending && execute(pending, true),
    reapply,
    discard,
  };
}
