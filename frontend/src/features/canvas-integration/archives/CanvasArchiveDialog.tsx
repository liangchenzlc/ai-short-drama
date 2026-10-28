import { loadCanvasArchiveBridge } from '@host/api/canvas/archives';
import { projectError, projectsApi } from '@host/api/projects/management';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { Dialog } from '@host/shared/ui/Dialog';
import type { CanvasArchiveResult } from '@host/types/canvas/archives';
import type { RemoteProject } from '@host/types/projects/management';
import { Alert, Button, Radio, Select, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
const archiveError = (cause: unknown) =>
  cause instanceof Error && cause.message ? cause.message : projectError(cause);

export function CanvasArchiveDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const { user, expired } = useAuth();
  const controller = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const [mode, setMode] = useState<'import' | 'export'>('import');
  const [projects, setProjects] = useState<RemoteProject[]>([]);
  const [projectId, setProjectId] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');
  const [pending, setPending] = useState<{
    title: string;
    count: number;
  } | null>(null);
  const [result, setResult] = useState<CanvasArchiveResult | null>(null);
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setLoadError('');
    setPending(null);
    setResult(null);
    if (!user || expired) {
      setLoading(false);
      setLoadError('请先登录后再导入导出画布。');
      return () => current.abort();
    }
    void (async () => {
      const entries: RemoteProject[] = [];
      let offset = 0;
      while (true) {
        const page = await projectsApi.list(offset, '', current.signal);
        entries.push(...page.items.filter((project) => project.workspaceMode === 'infinite_canvas'));
        if (offset + page.items.length >= page.total || !page.items.length) break;
        offset += page.items.length;
      }
      const bridge = await loadCanvasArchiveBridge();
      if (current.signal.aborted) return;
      setProjects(entries);
      setProjectId(entries[0]?.projectId ?? '');
      setPending(bridge.pendingArchive(user.id));
    })()
      .catch((cause) => {
        if (!current.signal.aborted) setLoadError(archiveError(cause));
      })
      .finally(() => {
        if (!current.signal.aborted) setLoading(false);
      });
    return () => current.abort();
  }, [user?.id, expired, revision]);

  async function run(action: 'import' | 'export' | 'recover', file?: File) {
    const signal = controller.current?.signal;
    if (busyRef.current || !user || expired || !signal || signal.aborted) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setResult(null);
    setProgress(
      action === 'export' ? '正在打包画布与媒体…' : action === 'recover' ? '正在核对原导入…' : '正在检查画布备份…',
    );
    try {
      const bridge = await loadCanvasArchiveBridge();
      signal.throwIfAborted();
      if (action === 'export') {
        const selected = projects.find((project) => project.projectId === projectId);
        if (!selected) throw new Error('请选择要导出的无限画布项目。');
        await bridge.exportArchive(selected.projectId, selected.name, user.id, signal);
        if (!signal.aborted) setProgress('画布 ZIP 已下载。');
      } else {
        const imported =
          action === 'recover'
            ? await bridge.recoverArchive(user.id, signal)
            : await bridge.importArchive(file!, user.id, signal, (state) => {
                if (!signal.aborted)
                  setProgress(`${state.message}${state.total ? ` ${state.completed}/${state.total}` : ''}`);
              });
        if (!signal.aborted) {
          setResult(imported);
          setPending(bridge.pendingArchive(user.id));
          setProgress(
            `${action === 'recover' ? '已核对' : '已导入'} ${imported.count} 个画布。${imported.remaining ? `还有 ${imported.remaining} 个未导入，请重新选择原ZIP继续。` : ''}`,
          );
          onImported();
        }
      }
    } catch (cause) {
      if (!signal.aborted) {
        setProgress('');
        setError(
          `${action === 'export' ? '导出' : action === 'recover' ? '核对' : '导入'}失败：${archiveError(cause)}`,
        );
        try {
          setPending((await loadCanvasArchiveBridge()).pendingArchive(user.id));
        } catch {
          /* 原错误保留，未覆盖待核对记录。 */
        }
      }
    } finally {
      busyRef.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  return (
    <Dialog title="画布导入导出" canClose={!busy} onClose={onClose} className="project-create-dialog">
      <div className="project-form">
        <Radio.Group
          value={mode}
          disabled={busy}
          onChange={(event) => {
            setMode(event.target.value);
            setError('');
            setProgress('');
          }}
          options={[
            { value: 'import', label: '导入画布' },
            { value: 'export', label: '导出项目' },
          ]}
        />
        {loading ? (
          <p role="status">
            <Spin size="small" /> 正在加载画布项目…
          </p>
        ) : loadError ? (
          <Alert
            type="error"
            showIcon
            message={loadError}
            action={<Button onClick={() => setRevision((value) => value + 1)}>重试</Button>}
          />
        ) : mode === 'import' ? (
          <>
            <p className="muted">选择画布 ZIP 备份，导入为自己的新项目。图片、音视频和绘图随备份恢复。</p>
            {pending && (
              <Alert
                type="warning"
                showIcon
                message={`“${pending.title}”有待核对导入。`}
                description="先核对已创建画布；有未完成项时选择原ZIP继续。"
                action={
                  <Button disabled={busy} onClick={() => void run('recover')}>
                    核对导入
                  </Button>
                }
              />
            )}
            <label htmlFor="canvas-archive-file">
              选择画布备份
              <input
                id="canvas-archive-file"
                type="file"
                accept="application/zip,.zip"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (file) void run('import', file);
                }}
              />
            </label>
          </>
        ) : projects.length ? (
          <>
            <label htmlFor="canvas-archive-project">
              导出项目
              <Select
                id="canvas-archive-project"
                aria-label="导出项目"
                value={projectId}
                disabled={busy}
                onChange={setProjectId}
                options={projects.map((project) => ({
                  value: project.projectId,
                  label: project.name,
                }))}
              />
            </label>
            <p className="muted">打包这个项目的全部已保存画布、节点设置、绘图和引用媒体。</p>
            <Button loading={busy} disabled={!projectId} onClick={() => void run('export')}>
              导出 ZIP
            </Button>
          </>
        ) : (
          <p className="muted">还没有可导出的无限画布项目。</p>
        )}
        {error && <Alert type="error" showIcon message={error} />}
        {progress && (
          <p role="status" aria-live="polite">
            {busy && <Spin size="small" />} {progress}
          </p>
        )}
        {result?.projectIds[0] && !result.remaining && (
          <a href={`/canvas-app/canvas/${encodeURIComponent(result.projectIds[0])}`}>打开导入画布</a>
        )}
        <div className="dialog-actions">
          <Button disabled={busy} onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
