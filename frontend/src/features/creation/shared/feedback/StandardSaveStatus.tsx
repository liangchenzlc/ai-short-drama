import { errorMessage } from '@host/api/http';
import { getStandardSaveSnapshot, subscribeStandardSaveState } from '@host/features/creation/shared/sessions/registry';
import { projectPath } from '@host/shared/navigation/paths';
import {
  StudioFeedbackDetails,
  useStudioFeedbackIssues,
  useStudioFeedbackReview,
} from '@host/shared/ui/StudioFeedback';
import type { StandardSaveEntry } from '@host/types/creation/shared/sessions/registry';
import { Button, Popover } from 'antd';
import { AlertCircle, Check, Cloud, CloudOff, LoaderCircle } from 'lucide-react';
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
function entryPath(entry: StandardSaveEntry) {
  const parts = entry.key.split(':');
  const purpose = parts.at(-1);
  const asset = parts[0] === 'asset' ? parts[1] : purpose === 'asset_image' ? parts.at(-2) : null;
  const shot =
    parts[0] === 'native-dialogue'
      ? parts.at(-1)
      : purpose === 'storyboard_image' || purpose === 'storyboard_video'
        ? parts.at(-2)
        : null;
  const stage =
    asset || entry.key.startsWith('asset-')
      ? 'assets'
      : shot || entry.key.startsWith('storyboard')
        ? 'storyboard'
        : entry.key.startsWith('assembly') || entry.key.startsWith('sound')
          ? 'assembly'
          : 'source';
  const query = new URLSearchParams();
  if (entry.key.startsWith('story-settings:')) query.set('writing', 'settings');
  if (asset) query.set('asset', asset);
  if (shot) query.set('shot', shot);
  return projectPath(entry.projectId!, stage) + (query.size ? `?${query}` : '');
}

export function StandardSaveStatus({ children }: { children?: ReactNode }) {
  const entries = useSyncExternalStore(subscribeStandardSaveState, getStandardSaveSnapshot);
  const [open, setOpen] = useState(false);
  const entry = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const owner = entry.current?.closest('dialog[open]') ?? null;
      const active = event.target instanceof Element ? event.target.closest('dialog[open]') : null;
      if (active !== owner || (!active && document.querySelector('dialog[open]'))) return;
      event.preventDefault();
      setOpen(false);
      requestAnimationFrame(() => entry.current?.focus({ preventScroll: true }));
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [open]);
  const feedbackIssues = useStudioFeedbackIssues().filter(
    (issue) => (issue.type === 'error' || issue.type === 'warning') && issue.scope !== 'chat',
  );
  useStudioFeedbackReview(() => setOpen(true));
  const [slow, setSlow] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const saving = entries.some((entry) => entry.status === 'saving');
  useEffect(() => {
    if (!saving) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), 800);
    return () => clearTimeout(timer);
  }, [saving]);
  const issues = entries.filter(
    (entry) => entry.status === 'conflict' || entry.status === 'error' || entry.hasUnpersisted,
  );
  const memoryOnly = issues.some((entry) => entry.hasUnpersisted);
  const conflict = issues.some((entry) => entry.status === 'conflict');
  const pending = entries.some((entry) => ['unsaved', 'local', 'saving', 'loading'].includes(entry.status));
  const label = memoryOnly
    ? '保存失败'
    : conflict
      ? `需处理 · ${issues.length} 项`
      : issues.length
        ? `待同步 · ${issues.length} 项`
        : feedbackIssues.length
          ? `需处理 · ${feedbackIssues.length} 项`
          : slow
            ? '保存中…'
            : pending
              ? '自动保存'
              : '已保存';
  const current = issues.length ? issues : entries.filter((entry) => entry.status !== 'idle');
  async function retry(entry: StandardSaveEntry) {
    if (busy) return;
    setBusy(entry.key);
    setError('');
    try {
      await (entry.retry ?? entry.flush)?.();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }
  const icon =
    memoryOnly || conflict || feedbackIssues.length ? (
      <AlertCircle size={15} />
    ) : issues.length ? (
      <CloudOff size={15} />
    ) : slow ? (
      <LoaderCircle size={15} />
    ) : pending ? (
      <Cloud size={15} />
    ) : (
      <Check size={15} />
    );
  return (
    <>
      <span className="sr-only" aria-live="polite">
        {issues.length ? label : ''}
      </span>
      <Popover
        rootClassName="standard-creation-popup"
        trigger="click"
        placement="bottom"
        open={open}
        onOpenChange={setOpen}
        title="保存状态"
        content={
          <div className="standard-save-details">
            {!current.length && <p>内容会自动保存。</p>}
            {current.map((entry) => (
              <div className="standard-save-entry" key={entry.key}>
                <div>
                  <strong>{entry.label ?? '编辑内容'}</strong>
                  <span>
                    {entry.hasUnpersisted
                      ? '本机与服务端均未保存成功，输入仍在当前应用中。'
                      : entry.status === 'conflict'
                        ? '版本不同，请核对内容。'
                        : entry.status === 'error'
                          ? '暂时无法完成同步，请查看原因。'
                          : entry.status === 'saving' || entry.status === 'unsaved' || entry.status === 'local'
                            ? '正在同步'
                            : entry.status === 'loading'
                              ? '正在读取'
                              : '已保存'}
                  </span>
                  {entry.message && <p>{entry.message}</p>}
                </div>
                <div className="standard-save-entry-actions">
                  {entry.status === 'error' && (entry.retry || entry.flush) && (
                    <Button
                      size="small"
                      disabled={!!busy}
                      loading={busy === entry.key}
                      onClick={() => void retry(entry)}
                    >
                      重试
                    </Button>
                  )}
                  {entry.projectId && (
                    <Button
                      size="small"
                      type="link"
                      onClick={() => {
                        setOpen(false);
                        navigate(entryPath(entry));
                      }}
                    >
                      查看内容
                    </Button>
                  )}
                </div>
              </div>
            ))}
            {error && <p role="alert">{error}</p>}
            <StudioFeedbackDetails />
            {children}
          </div>
        }
      >
        <Button
          ref={entry}
          type="text"
          size="small"
          className={`creation-save-state standard-save-status${issues.length || feedbackIssues.length ? ' has-issues' : ''}`}
          aria-label={`保存状态：${label}`}
          aria-expanded={open}
          icon={icon}
          title={label}
        />
      </Popover>
    </>
  );
}
