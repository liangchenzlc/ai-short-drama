import { ScriptSceneEditor } from '@host/features/creation/writing/editor/ScriptSceneEditor';
import type { WritingSession } from '@host/features/creation/writing/sessions/writing-session';
import { StorySettingsEditor } from '@host/features/creation/writing/story-settings/StorySettingsEditor';
import type { WritingTask } from '@host/types/creation/writing/documents';
import type { ScriptSelection, WritingTarget } from '@host/types/creation/writing/rewrite/writing-requests';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { WritingSnapshot } from '@host/types/creation/writing/sessions/writing-session';
import { LoaderCircle } from 'lucide-react';
import type { KeyboardEvent, ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
export function ScriptDesk({
  projectId,
  writing,
  session,
  readOnly,
  onRewrite,
  selection,
  pendingTarget,
  submitting,
  rewriteDisabled,
  onStop,
  stopping,
  feedback,
  registerSettings,
}: {
  projectId: string;
  writing: WritingSnapshot;
  session: WritingSession;
  readOnly: boolean;
  onRewrite: (selection: ScriptSelection) => void;
  selection: ScriptSelection | null;
  pendingTarget?: WritingTarget;
  submitting: boolean;
  rewriteDisabled: boolean;
  onStop: (task: WritingTask) => void;
  stopping: string | null;
  feedback: ReactNode;
  registerSettings: (barrier: NavigationBarrier | null) => void;
}) {
  const [parameters, setParameters] = useSearchParams();
  const tab = parameters.get('writing') === 'settings' ? 'settings' : 'script';
  function selectTab(next: string) {
    setParameters(
      (previous) => {
        const copy = new URLSearchParams(previous);
        if (next === 'settings') copy.set('writing', next);
        else copy.delete('writing');
        return copy;
      },
      { replace: true },
    );
  }
  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === 'Home' ? 'settings' : event.key === 'End' ? 'script' : tab === 'script' ? 'settings' : 'script';
    selectTab(next);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#writing-${next}-tab`)?.focus();
  }
  const task = writing.activeWritingTasks.find((item) => item.target === 'script');
  const locked = readOnly || !!task || pendingTarget === 'script';
  const progress = task ? (
    <>
      <span className="sd-task-status" role="status">
        <LoaderCircle size={13} className="sd-spinner" />
        {task.operation === 'script_rewrite' ? '正在改写选区' : '正在更新剧本'}
      </span>
      <button
        type="button"
        className="sd-btn is-quiet"
        disabled={stopping !== null}
        aria-busy={stopping === task.task_id}
        onClick={() => onStop(task)}
      >
        {stopping === task.task_id ? '停止中…' : '停止'}
      </button>
    </>
  ) : pendingTarget === 'script' ? (
    <span className="sd-task-status" role="status">
      {submitting ? '正在提交改写' : '改写请求待核对'}
    </span>
  ) : null;
  return (
    <div className="sd-desk">
      <section className="sd-doc" aria-label="剧本">
        <header className="sd-doc-head">
          <h1>剧本</h1>
          <div className="sd-head-actions">
            {progress}
            {readOnly && <span className="sd-readonly">只读</span>}
          </div>
        </header>
        <div className="sd-tabs" role="tablist" aria-label="剧本内容">
          {[
            { key: 'settings', label: '故事设定' },
            { key: 'script', label: '剧本正文' },
          ].map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              id={`writing-${item.key}-tab`}
              aria-controls={`writing-${item.key}-pane`}
              aria-selected={tab === item.key}
              tabIndex={tab === item.key ? 0 : -1}
              onClick={() => selectTab(item.key)}
              onKeyDown={navigateTabs}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div
          id="writing-settings-pane"
          className="sd-tab-pane"
          role="tabpanel"
          aria-labelledby="writing-settings-tab"
          hidden={tab !== 'settings'}
        >
          <StorySettingsEditor projectId={projectId} readOnly={readOnly} registerBarrier={registerSettings} />
        </div>
        <div
          id="writing-script-pane"
          className="sd-tab-pane"
          role="tabpanel"
          aria-labelledby="writing-script-tab"
          hidden={tab !== 'script'}
        >
          <ScriptSceneEditor
            script={writing.script}
            readOnly={locked}
            rewriteDisabled={rewriteDisabled}
            loaded={writing.loaded}
            frozenSelection={selection}
            onChange={(content) => session.edit('script', content)}
            onRewrite={onRewrite}
          />
        </div>
        {feedback}
      </section>
    </div>
  );
}
