import { ApiError, errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { createSkillDraft, downloadSkillText, skillContent } from '@host/features/skills/imports/skill-draft';
import {
  matchesSubmission,
  modeChoice,
  modeOptions,
  modeValues,
  packageChanges,
} from '@host/features/skills/library/skill-model';
import { SkillFileBrowser } from '@host/features/skills/resources/SkillFileBrowser';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import { confirmAction } from '@host/shared/ui/confirm';
import type { AgentSkillDetail, AgentSkillImportPreview } from '@host/types/assistant/engine';
import type { SkillDraft } from '@host/types/skills/imports/skill-draft';
import type { SkillModeChoice } from '@host/types/skills/library/skill-model';
import { Button, Segmented, Skeleton, Typography } from 'antd';
import { Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
export function SkillImportDialog({
  accountId,
  target,
  restored,
  sameNames,
  storageIssue,
  onPersist,
  onSaved,
  onClose,
  onBusy,
  onDiscard,
}: {
  accountId: string;
  target: AgentSkillDetail | null;
  restored: SkillDraft | null;
  sameNames: string[];
  storageIssue: string;
  onPersist: (draft: SkillDraft, settled?: boolean) => boolean;
  onSaved: (detail: AgentSkillDetail) => void;
  onClose: () => void;
  onBusy: (busy: boolean) => void;
  onDiscard: () => void;
}) {
  const [draft, setDraft] = useState(restored);
  const [preview, setPreview] = useState<AgentSkillImportPreview | null>(null);
  const [issue, setIssue] = useState('');
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [latest, setLatest] = useState<AgentSkillDetail | null>(null);
  const [viewLatest, setViewLatest] = useState(false);
  const [duplicate, setDuplicate] = useState(false);
  const [changes, setChanges] = useState<Awaited<ReturnType<typeof packageChanges>> | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const check = useRef<AbortController | null>(null);
  const live = useRef(true);
  const lock = useRef(false);
  const base = draft?.base ?? target;
  const choice = modeChoice(draft?.values.modes ?? ['standard', 'infinite_canvas']);
  const issues = [...(preview?.issues ?? []), ...(preview?.mode_issues[choice] ?? [])];
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      check.current?.abort();
    };
  }, []);
  useEffect(() => {
    setDuplicate(false);
    if (!draft || draft.base) return;
    const controller = new AbortController();
    skillsApi
      .skills(0, controller.signal, {
        source: 'personal',
        query: draft.values.name,
      })
      .then((page) => {
        if (!controller.signal.aborted) setDuplicate(page.items.some((item) => item.name === draft.values.name));
      })
      .catch(() => {
        /* The server still creates a new identity; a duplicate notice is optional. */
      });
    return () => controller.abort();
  }, [draft?.key, draft?.values.name, draft?.base]);
  useEffect(() => {
    let current = true;
    setChanges(null);
    if (draft?.base && draft.imported) {
      void packageChanges(draft.base, skillContent(draft.values))
        .then((value) => {
          if (current) setChanges(value);
        })
        .catch((cause) => {
          if (current) setIssue(errorMessage(cause));
        });
    }
    return () => {
      current = false;
    };
  }, [draft?.base, draft?.values, draft?.imported]);
  function remember(value: SkillDraft, settled = false) {
    setDraft(value);
    return onPersist(value, settled);
  }
  function markBusy(value: boolean) {
    lock.current = value;
    if (live.current) {
      setBusy(value);
      onBusy(value);
    }
  }
  async function choose(file: File | undefined) {
    if (!file || busy || draft?.pending) return;
    check.current?.abort();
    setIssue('');
    setChecking(false);
    setDraft(null);
    setPreview(null);
    setLatest(null);
    setViewLatest(false);
    if (!/\.(md|zip)$/i.test(file.name) || !file.size || file.size > 4 * 1048576) {
      setIssue('请选择非空且不超过 4 MiB 的 Markdown 或 ZIP 文件');
      return;
    }
    const controller = new AbortController();
    check.current = controller;
    setChecking(true);
    try {
      const result = await skillsApi.previewSkillImport(file, controller.signal);
      if (controller.signal.aborted || !live.current) return;
      const initial = createSkillDraft(target ?? undefined, result.draft);
      initial.values.modes = target?.modes ?? ['standard', 'infinite_canvas'];
      initial.values.enabled = target?.enabled ?? true;
      if (target && !result.tags_provided) initial.values.tags = target.tags;
      setDraft({
        ...initial,
        imported: true,
        tags_provided: result.tags_provided,
      });
      setPreview(result);
    } catch (cause) {
      if (!controller.signal.aborted && live.current) setIssue(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && live.current) setChecking(false);
    }
  }
  async function send(value: SkillDraft) {
    const pending = value.pending!;
    try {
      const { enabled: _enabled, ...content } = pending.body;
      const saved = value.base
        ? await skillsApi.updateSkill(value.base.id, {
            ...content,
            row_version: pending.row_version!,
          })
        : await skillsApi.createSkill(pending.body, pending.key);
      if (live.current) onSaved(saved);
    } catch (cause) {
      if (!live.current) return;
      const conflict = cause instanceof ApiError && cause.status === 409;
      const unknown = !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;
      if (conflict) {
        remember({ ...value, pending: null, conflict: true }, true);
        setIssue('版本已变化，导入文件已保留');
      } else if (unknown) setIssue('导入结果待核对');
      else {
        remember({ ...value, pending: null }, true);
        setIssue(errorMessage(cause));
      }
    }
  }
  async function submit() {
    if (lock.current || !draft || draft.pending || draft.conflict || issues.length) return;
    if (draft.base && !changes) return;
    if (
      changes?.removed.length &&
      !(await confirmAction(`此次更新将移除：${changes.removed.join('、')}。历史任务继续使用原版本。`, {
        title: '确认完整包替换',
        confirmText: '确认更新',
        danger: true,
      }))
    )
      return;
    markBusy(true);
    setIssue('');
    try {
      const body = skillContent(draft.values);
      if (draft.base && !draft.tags_provided) delete body.tags;
      const value = {
        ...draft,
        pending: {
          key: draft.key,
          body,
          row_version: draft.base?.row_version ?? null,
        },
      };
      if (remember(value)) await send(value);
      else setIssue('原请求尚未保存到浏览器，请恢复存储后重试');
    } catch (cause) {
      if (live.current) setIssue(errorMessage(cause));
    } finally {
      markBusy(false);
    }
  }
  async function reconcile() {
    if (lock.current || !draft?.pending) return;
    markBusy(true);
    setIssue('');
    try {
      if (!draft.base) {
        try {
          const receipt = await skillsApi.skillRequest(draft.pending.key);
          if (live.current) onSaved(receipt);
          return;
        } catch (cause) {
          if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
        }
      } else {
        const server = await skillsApi.skill(draft.base.id);
        if (!live.current) return;
        if (await matchesSubmission(server, draft.pending.body)) {
          onSaved(server);
          return;
        }
        if (server.row_version !== draft.pending.row_version) {
          remember({ ...draft, pending: null, conflict: true }, true);
          setLatest(server);
          setIssue('当前版本与导入文件不同，请核对后继续');
          return;
        }
      }
      if (onPersist(draft)) await send(draft);
      else setIssue('原请求尚未保存到浏览器，请恢复存储后重试');
    } catch (cause) {
      if (live.current) setIssue(`核对未完成：${errorMessage(cause)}`);
    } finally {
      markBusy(false);
    }
  }
  async function readLatest() {
    if (lock.current || !draft?.base) return;
    markBusy(true);
    try {
      const value = await skillsApi.skill(draft.base.id);
      if (live.current) setLatest(value);
    } catch (cause) {
      if (live.current) setIssue(errorMessage(cause));
    } finally {
      markBusy(false);
    }
  }
  async function rebase() {
    if (
      !draft ||
      !latest ||
      !(await confirmAction('已核对当前版本，将用导入的完整包替换它？', {
        title: '更新 Skill',
        confirmText: '按当前版本更新',
        danger: true,
      }))
    )
      return;
    remember({ ...draft, base: latest, conflict: false, pending: null }, true);
    setLatest(null);
    setIssue('');
    setViewLatest(false);
  }
  const shown =
    viewLatest && latest
      ? {
          instructions: latest.instructions,
          resources: latest.resources.map((resource) => ({
            ...resource,
            content: null,
            origin_version: latest.content_version,
          })),
        }
      : draft?.values;
  return (
    <Dialog
      title={base ? '更新 Skill' : '导入 Skill'}
      className="skill-preview-dialog"
      canClose={!busy}
      onClose={onClose}
    >
      {(issue || storageIssue || draft?.pending || draft?.conflict) && (
        <StudioFeedback
          type="warning"
          persistent
          noticeKey="skill-import"
          message={storageIssue || issue || (draft?.pending ? '导入结果待核对' : '版本已变化，导入文件已保留')}
        />
      )}
      <input
        ref={input}
        type="file"
        accept=".md,.zip"
        hidden
        onChange={(event) => {
          void choose(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
      {!draft && !checking && (
        <div
          className="skill-import-drop"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            void choose(event.dataTransfer.files[0]);
          }}
        >
          <Upload size={28} />
          <p>选择或拖入 Markdown / ZIP 文件</p>
          <Button type="primary" onClick={() => input.current?.click()}>
            选择文件
          </Button>
          <details>
            <summary>格式要求</summary>
            <p>SKILL.md 提供名称、用途和正文。附加资料一起打包为 ZIP；暂不接受脚本。</p>
            <p>上传最多 4 MiB，展开后最多 32 个文件、合计 1 MiB。</p>
          </details>
        </div>
      )}
      {checking && (
        <div className="skill-file-state" role="status">
          <p>正在检查文件…</p>
          <Skeleton active />
        </div>
      )}
      {draft && (
        <>
          <div className="skill-preview-summary">
            <h3>{draft.values.name}</h3>
            <Typography.Paragraph
              className="skill-preview-purpose"
              ellipsis={{
                rows: 2,
                expandable: 'collapsible',
                symbol: (expanded) => (expanded ? '收起用途' : '展开用途'),
              }}
            >
              {draft.values.description}
            </Typography.Paragraph>
            <div className="skill-mode-control">
              <span>适用模式</span>
              <Segmented
                aria-label="适用模式"
                options={modeOptions}
                value={choice}
                disabled={busy || !!draft.pending || draft.conflict}
                onChange={(value) =>
                  setDraft({
                    ...draft,
                    values: {
                      ...draft.values,
                      modes: modeValues(value as SkillModeChoice),
                    },
                  })
                }
              />
            </div>
            {!base && (duplicate || sameNames.includes(draft.values.name)) && (
              <p role="status">已有同名 Skill，将新增一份</p>
            )}
            {issues.length > 0 && (
              <div role="alert" className="skill-inline-issue">
                <p>{issues[0]}</p>
                {issues.length > 1 && (
                  <details>
                    <summary>查看全部问题</summary>
                    {issues.slice(1).map((value) => (
                      <p key={value}>{value}</p>
                    ))}
                  </details>
                )}
              </div>
            )}
            {changes && (
              <details className="skill-package-changes">
                <summary>查看变更</summary>
                {(['added', 'changed', 'removed'] as const).map(
                  (kind) =>
                    changes[kind].length > 0 && (
                      <p key={kind}>
                        {{ added: '新增', changed: '修改', removed: '移除' }[kind]}：{changes[kind].join('、')}
                      </p>
                    ),
                )}
                {!changes.added.length && !changes.changed.length && !changes.removed.length && <p>文件内容未变化</p>}
              </details>
            )}
            {latest && (
              <div className="skill-conflict-options">
                <span>当前版本：{latest.name}</span>
                <Button size="small" onClick={() => setViewLatest((value) => !value)}>
                  {viewLatest ? '查看导入文件' : '查看当前文件'}
                </Button>
                <Button size="small" disabled={busy} onClick={() => void rebase()}>
                  按当前版本更新
                </Button>
              </div>
            )}
          </div>
          {shown && (
            <SkillFileBrowser
              identity={`${accountId}:${draft.key}:${viewLatest ? latest?.content_version : 'import'}`}
              skillId={viewLatest ? latest?.id : draft.base?.id}
              instructions={shown.instructions}
              resources={shown.resources}
            />
          )}
        </>
      )}
      <footer className="skill-dialog-footer">
        {draft && (restored || draft.pending) && (
          <Button
            disabled={busy}
            onClick={() => downloadSkillText('skill-recovery.json', JSON.stringify(draft, null, 2))}
          >
            下载记录
          </Button>
        )}
        {restored && (
          <Button danger disabled={busy} onClick={onDiscard}>
            清除记录
          </Button>
        )}
        <Button disabled={busy || !!draft?.pending || !!draft?.conflict} onClick={() => input.current?.click()}>
          重新选择
        </Button>
        <span className="skill-footer-spacer" />
        <Button disabled={busy} onClick={onClose}>
          关闭
        </Button>
        {draft?.pending && (
          <Button type="primary" loading={busy} onClick={() => void reconcile()}>
            核对导入结果
          </Button>
        )}
        {draft?.conflict && (
          <Button disabled={busy} onClick={() => void readLatest()}>
            读取当前版本
          </Button>
        )}
        {storageIssue && draft && (
          <Button disabled={busy} onClick={() => onPersist(draft)}>
            重试保存记录
          </Button>
        )}
        {draft && (
          <Button
            type="primary"
            loading={busy}
            disabled={checking || !!draft.pending || draft.conflict || issues.length > 0 || (!!draft.base && !changes)}
            onClick={() => void submit()}
          >
            {base ? '确认更新' : '确认导入'}
          </Button>
        )}
      </footer>
    </Dialog>
  );
}
