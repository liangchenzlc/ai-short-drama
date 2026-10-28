import { StarOutlined } from '@ant-design/icons';
import { projectAssetsApi } from '@host/api/creation/assets/management';
import { storyboardApi } from '@host/api/creation/storyboard/shots';
import { errorMessage } from '@host/api/http';
import { withCreationView } from '@host/features/assistant/runtime/agent-navigation';
import { ASSISTANT_BUSINESS_CHANGED } from '@host/features/assistant/runtime/assistant-business-events';
import { StandardSaveStatus } from '@host/features/creation/shared/feedback/StandardSaveStatus';
import {
  creationScope,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { CreationWorkspace } from '@host/features/creation/workspace/layout/CreationWorkspace';
import {
  assistantView,
  replaceCreationView,
  selectAssistantConversation,
  selectCreationObject,
  setAssistantOpen,
} from '@host/features/creation/workspace/navigation/assistant-navigation';
import { creationStages } from '@host/features/creation/workspace/navigation/StageNav';
import { TopStepNav } from '@host/features/creation/workspace/navigation/TopStepNav';
import { useProjectWriting } from '@host/features/creation/writing/sessions/useProjectWriting';
import { hasUnsettledWriting } from '@host/features/creation/writing/sessions/writing-navigation';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { ProjectNameEditor } from '@host/features/projects/management/ProjectNameEditor';
import { preloadable } from '@host/shared/async/preloadable';
import { projectPath } from '@host/shared/navigation/paths';
import { Icon } from '@host/shared/ui/Icon';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AssistantMessageContext, AssistantReference } from '@host/types/assistant/api';
import type { AgentSubject } from '@host/types/assistant/references/agent-scope';
import type { AssistantBusinessChange } from '@host/types/assistant/runtime/assistant-business-events';
import type { CreationPreferences, CreationStage as StageId } from '@host/types/creation/workspace/model';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { RemoteProject } from '@host/types/projects/management';
import type { ProjectSession } from '@host/types/projects/model';
import { Button, Skeleton, Spin } from 'antd';
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ComponentProps } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import '../../features/assistant/runtime/agents.css';
import './index.css';
const loadAssemblyStage = () => import('./assembly/index');
const loadSourceStage = () => import('./script/index');
const loadAssetsStage = () => import('./assets/index');
const loadStoryboardStage = () => import('./storyboard/index');
const AssemblyStage = preloadable(() => loadAssemblyStage().then((module) => ({ default: module.AssemblyStage })));
const CreativeAssistantPanel = lazy(() =>
  import('../../features/assistant/panel/CreativeAssistantPanel').then((module) => ({
    default: module.CreativeAssistantPanel,
  })),
);
const ImageGroupSource = lazy(() =>
  import('../../features/media/image-groups/ImageGroupSource').then((module) => ({ default: module.ImageGroupSource })),
);
const SourceStage = preloadable(() => loadSourceStage().then((module) => ({ default: module.SourceStage })));
const AssetsStage = preloadable(() => loadAssetsStage().then((module) => ({ default: module.AssetsStage })));
const StoryboardStage = preloadable(() =>
  loadStoryboardStage().then((module) => ({ default: module.StoryboardStage })),
);

export async function preloadCreationStage(stage?: string): Promise<void> {
  if (stage === 'source' || stage === 'script') await SourceStage.preload();
  else if (stage === 'assets') await AssetsStage.preload();
  else if (stage === 'storyboard' || stage === 'video') await StoryboardStage.preload();
  else if (stage === 'assembly') await AssemblyStage.preload();
}

export function CreationPage(props: ComponentProps<typeof CreationPageBody>) {
  const { user } = useAuth();
  return (
    <CreationPageBody
      key={`${user?.id ?? 'anonymous'}:${props.session.projectId}:${props.project.projectId}`}
      {...props}
    />
  );
}
function CreationPageBody({
  session,
  project,
  onProjectUpdated,
}: {
  session: ProjectSession;
  project: RemoteProject;
  onProjectUpdated: (project: RemoteProject) => void;
}) {
  const auth = useAuth();
  const [value, setValue] = useState<CreationPreferences>({
    models: {
      script: '',
      analysis: '',
      assetImage: '',
      storyboardText: '',
      storyboardImage: '',
      video: '',
    },
  });
  const storyboardBarrier = useRef<NavigationBarrier | null>(null);
  const extractionBarrier = useRef<NavigationBarrier | null>(null);
  const assemblyBarrier = useRef<NavigationBarrier | null>(null);
  const nameBarrier = useRef<NavigationBarrier | null>(null);
  const registerNameBarrier = useCallback((barrier: NavigationBarrier | null) => {
    nameBarrier.current = barrier;
  }, []);
  const sourceBarrier = useRef<NavigationBarrier | null>(null);
  const getStoryboardBarrier = useRef(() => ({
    hasUnsettled: () =>
      !!nameBarrier.current?.hasUnsettled() ||
      !!sourceBarrier.current?.hasUnsettled() ||
      !!storyboardBarrier.current?.hasUnsettled() ||
      !!extractionBarrier.current?.hasUnsettled() ||
      !!assemblyBarrier.current?.hasUnsettled(),
    flush: async () => {
      for (const barrier of [
        nameBarrier.current,
        sourceBarrier.current,
        storyboardBarrier.current,
        extractionBarrier.current,
        assemblyBarrier.current,
      ]) {
        if (barrier?.hasUnsettled() && !(await barrier.flush())) return false;
      }
      return true;
    },
  })).current;
  const writing = useProjectWriting(session.projectId, getStoryboardBarrier);
  const workflow = value;
  const { stage } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const updateParameters = useCallback(
    (update: (previous: URLSearchParams) => URLSearchParams) => {
      replaceCreationView(projectPath(session.projectId), navigate, update);
    },
    [session.projectId, navigate],
  );
  const parameters = assistantView(location.search);
  const step = stage === 'script' ? 'source' : (creationStages.find((item) => item.id === stage)?.id ?? 'source');
  const [visitedStages, setVisitedStages] = useState(() => new Set([step]));
  useEffect(() => {
    setVisitedStages((previous) => (previous.has(step) ? previous : new Set([...previous, step])));
  }, [step]);
  const assistantOpen = parameters.get('assistant') === 'open';
  const conversationId = parameters.get('conversation') ?? undefined;
  const [assistantMounted, setAssistantMounted] = useState(assistantOpen);
  useEffect(() => {
    if (assistantOpen) setAssistantMounted(true);
  }, [assistantOpen]);
  useEffect(() => {
    if (parameters.toString() !== new URLSearchParams(location.search).toString())
      updateParameters((previous) => assistantView(previous.toString()));
  }, [location.search, updateParameters]);
  const [subjects, setSubjects] = useState<Record<string, AgentSubject | null>>({});
  const objectParameter = step === 'assets' ? 'asset' : step === 'storyboard' ? 'shot' : null;
  const selectedObjectId = objectParameter ? parameters.get(objectParameter) : null;
  const selectedSubject = subjects[step];
  const subject: AgentSubject | null = selectedObjectId
    ? selectedSubject?.id === selectedObjectId
      ? selectedSubject
      : {
          type: step === 'assets' ? 'asset' : 'shot',
          id: selectedObjectId,
          label: step === 'assets' ? '当前素材' : '当前分镜',
        }
    : null;
  const objectView = useRef({ step, updateParameters });
  objectView.current = { step, updateParameters };
  const selectSubject = useCallback((next: AgentSubject | null) => {
    const { step, updateParameters } = objectView.current;
    setSubjects((previous) => {
      const old = previous[step];
      return old?.id === next?.id && old?.label === next?.label && old?.revision === next?.revision
        ? previous
        : { ...previous, [step]: next };
    });
    updateParameters((previous) => selectCreationObject(previous.toString(), step, next?.id ?? null));
  }, []);
  const closeAssistant = useCallback(() => {
    updateParameters((previous) => setAssistantOpen(previous.toString(), false));
  }, [updateParameters]);
  useEffect(() => {
    if (stage !== step)
      navigate(withCreationView(projectPath(session.projectId, step), location.search), { replace: true });
  }, [stage, step, session.projectId, navigate, location.search]);
  // 模型选择只保留界面状态；持久偏好由 ModelPreferencesProvider 串行保存。
  const latest = useRef(value);
  const [localError, setLocalError] = useState('');
  const readOnly = false;
  const writingReadOnly =
    readOnly || !writing.loaded || writing.status === 'loading' || !!writing.recoverable || !!writing.recoveryBlocked;
  function goToStep(next: StageId) {
    navigate(withCreationView(projectPath(session.projectId, next), assistantView(window.location.search).toString()));
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  const assistantContext = useRef({ step, subject, writing, readOnly });
  assistantContext.current = { step, subject, writing, readOnly };
  const [assistantRevision, setAssistantRevision] = useState(0);
  useEffect(() => {
    const refresh = (event: Event) => {
      const change = (event as CustomEvent<AssistantBusinessChange>).detail;
      if (change?.projectId !== session.projectId) return;
      setAssistantRevision((value) => value + 1);
      if (change.kinds.includes('script')) void assistantContext.current.writing.session.refreshFromServer();
    };
    window.addEventListener(ASSISTANT_BUSINESS_CHANGED, refresh);
    return () => window.removeEventListener(ASSISTANT_BUSINESS_CHANGED, refresh);
  }, [session.projectId]);
  const prepareAssistantContext = useCallback(
    async (
      _include: boolean,
      references: readonly AssistantReference[] = [],
    ): Promise<AssistantMessageContext | null> => {
      if (!references.length) return null;
      const captured = assistantContext.current;
      if (
        !captured.writing.loaded ||
        captured.writing.recoverable ||
        captured.writing.recoveryBlocked ||
        !(await captured.writing.session.flush())
      )
        throw new Error('当前正文尚未保存成功，请处理保存提示后再发送。');
      const barrier = getStoryboardBarrier();
      if (barrier.hasUnsettled() && !(await barrier.flush()))
        throw new Error('当前作品尚未保存成功，请处理编辑或冲突提示后再发送。');
      const current = assistantContext.current;
      if (
        current.step !== captured.step ||
        current.subject?.id !== captured.subject?.id ||
        hasUnsettledWriting(current.writing.session.getSnapshot()) ||
        barrier.hasUnsettled()
      )
        throw new Error('作品或选择对象在准备期间发生变化，消息草稿已保留，请重新发送。');
      const revision = current.writing.session.getSnapshot().contentVersion;
      const context: AssistantMessageContext = {
        kind: 'project',
        id: session.projectId,
        revision,
        stage: captured.step,
        include_document: false,
      };
      if (captured.step === 'storyboard' || captured.step === 'assembly') {
        const page = await storyboardApi(session.projectId).shots();
        context.storyboard_revision = page.storyboard_version;
      }
      context.selected = await Promise.all(
        references.map(async (reference) => {
          if (reference.kind === 'node') throw new Error('当前作品不能引用画布节点。');
          const selected =
            reference.kind === 'asset'
              ? await projectAssetsApi.detail(reference.id)
              : (await storyboardApi(session.projectId).shot(reference.id)).shot;
          if (reference.revision && selected.row_version !== reference.revision)
            throw new Error('引用对象已更新，请重新选择引用后发送。');
          return {
            kind: reference.kind,
            id: selected.id,
            revision: selected.row_version,
          };
        }),
      );
      const settled = assistantContext.current;
      if (
        settled.step !== captured.step ||
        settled.subject?.id !== captured.subject?.id ||
        hasUnsettledWriting(settled.writing.session.getSnapshot()) ||
        barrier.hasUnsettled() ||
        settled.writing.session.getSnapshot().contentVersion !== revision
      )
        throw new Error('作品在读取期间发生变化，消息草稿已保留，请重新发送。');
      return context;
    },
    [session.projectId, getStoryboardBarrier],
  );
  function update(change: CreationPreferences | ((v: CreationPreferences) => CreationPreferences)) {
    if (readOnly) return;
    const changed = typeof change === 'function' ? change(latest.current) : change;
    const next = changed;
    latest.current = next;
    setValue(next);
  }
  function exportDraft() {
    const content = JSON.stringify(
      {
        script: writing.script,
        script_id: writing.scriptId,
        content_version: writing.contentVersion,
      },
      null,
      2,
    );
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `project-${session.projectId}-draft.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportRecovery() {
    try {
      const content = writing.session.exportRecovered();
      if (!content) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `project-${session.projectId}-recovery.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setLocalError('无法下载本机恢复记录，请检查浏览器存储权限后重试。');
    }
  }
  async function reloadWriting() {
    try {
      backupWriting();
      await writing.session.reloadFromServer();
    } catch (cause) {
      setLocalError(errorMessage(cause));
    }
  }
  function backupWriting() {
    saveRecovery(creationScope(`writing-before-replace:${session.projectId}`), {
      current: writing.session.getSnapshot(),
      recovery: writing.session.exportRecovered(),
    });
  }
  function exportReplacement() {
    try {
      const content = readRecoveryText(creationScope(`writing-before-replace:${session.projectId}`));
      if (!content) {
        setLocalError('当前作品没有替换前的本机副本。');
        return;
      }
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `project-${session.projectId}-before-replace.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setLocalError(errorMessage(cause));
    }
  }
  return (
    <div className="creation-page web-episode">
      <TopStepNav currentStage={step} onStageChange={goToStep} />
      <header className="creation-top">
        <div className="creation-project-location">
          <Button
            type="text"
            className="detail-back"
            aria-label="返回项目列表"
            icon={<Icon name="back" size={16} />}
            onClick={() => navigate('/projects')}
          />
          <span className="creation-brand-mark" aria-hidden="true">
            <Icon name="film" size={22} />
          </span>
          <ProjectNameEditor
            project={project}
            readOnly={readOnly}
            onUpdated={onProjectUpdated}
            registerBarrier={registerNameBarrier}
          />
          <span className="creation-mode-label" title={readOnly ? '当前为只读模式，可查看当前作品内容。' : undefined}>
            {readOnly ? '只读模式' : '创作模式'}
          </span>
        </div>
        <div className="creation-project-actions">
          <StandardSaveStatus>
            {(writing.message || writing.recoverable || writing.recoveryBlocked) && (
              <details className="standard-writing-review">
                <summary>内容同步核对</summary>
                <p>
                  {writing.message ||
                    (writing.recoveryBlocked
                      ? '本机恢复记录无法读取，原始内容仍保留。'
                      : '本机内容与服务端版本不同，请选择继续使用的内容。')}
                </p>
                <Button size="small" disabled={writing.busy} onClick={exportDraft}>
                  下载当前内容
                </Button>
                {(writing.recoverable || writing.recoveryBlocked) && (
                  <Button size="small" disabled={writing.busy} onClick={exportRecovery}>
                    下载完整恢复记录
                  </Button>
                )}
                {writing.recoverable && !writing.recoveryBlocked && (
                  <>
                    <pre>{writing.session.getRecoveredDraft()?.script}</pre>
                    <Button
                      size="small"
                      disabled={readOnly || writing.busy}
                      onClick={() => void writing.session.restoreRecovered(!!writing.recoveryChanged)}
                    >
                      保留本机内容继续编辑
                    </Button>
                  </>
                )}
                {writing.status !== 'conflict' && (
                  <Button size="small" disabled={writing.busy} onClick={() => void writing.session.retry()}>
                    重试同步
                  </Button>
                )}
                <Button
                  size="small"
                  disabled={writing.busy}
                  aria-label="使用服务端版本"
                  aria-busy={writing.status === 'loading'}
                  loading={writing.status === 'loading'}
                  onClick={() => void reloadWriting()}
                >
                  使用服务端版本
                </Button>
                {writing.recoveryBlocked && (
                  <Button
                    size="small"
                    disabled={writing.busy}
                    onClick={() => {
                      try {
                        backupWriting();
                        writing.session.discardRecovered();
                      } catch (cause) {
                        setLocalError(errorMessage(cause));
                      }
                    }}
                  >
                    保留服务端内容并清除损坏记录
                  </Button>
                )}
              </details>
            )}
            <Button type="link" size="small" onClick={exportReplacement}>
              下载替换前的本机副本
            </Button>
          </StandardSaveStatus>
          <Button
            className="creation-assistant-entry"
            aria-label="AI 创作助手"
            icon={<StarOutlined aria-hidden="true" />}
            aria-expanded={assistantOpen}
            aria-controls="agent-conversation-pane"
            onClick={() =>
              updateParameters((previous) =>
                setAssistantOpen(previous.toString(), previous.get('assistant') !== 'open'),
              )
            }
          >
            AI 创作助手
          </Button>
        </div>
      </header>
      <div className="creation-layout">
        <CreationWorkspace
          accountId={auth.user?.id}
          workRequest={0}
          stage={step}
          subject={subject}
          onSubject={selectSubject}
          assistantOpen={assistantOpen}
          assistantPanel={
            assistantMounted ? (
              <Suspense
                fallback={
                  <div role="status" className="studio-empty">
                    正在载入 AI 创作助手…
                  </div>
                }
              >
                <CreativeAssistantPanel
                  executionTarget={{
                    mode: 'standard',
                    project_id: session.projectId,
                  }}
                  layout="standard"
                  open={assistantOpen}
                  key={`${auth.user?.id ?? 'anonymous'}:${session.projectId}`}
                  projectId={session.projectId}
                  projectTitle={project.name}
                  accountId={auth.user?.id}
                  readOnly={readOnly}
                  onClose={closeAssistant}
                  selectedConversationId={conversationId}
                  onConversationChange={(id) =>
                    updateParameters((previous) => selectAssistantConversation(previous.toString(), id))
                  }
                  onOpenModelSettings={() => navigate('/ai_config')}
                  contextPreview={{
                    key: `${session.projectId}:${step}:${subject?.id ?? ''}`,
                    label: `${project.name}${subject ? ` · ${subject.label}` : ''}`,
                  }}
                  mentionReferences={
                    subject
                      ? [
                          {
                            kind: subject.type,
                            id: subject.id,
                            name: subject.label,
                            revision: subject.revision,
                          },
                        ]
                      : []
                  }
                  prepareContext={prepareAssistantContext}
                />
              </Suspense>
            ) : null
          }
        >
          {localError && (
            <StudioFeedback
              type="error"
              title="作品内容操作"
              message={localError}
              noticeKey={`creation-operation:${session.projectId}`}
            />
          )}
          {!writing.loaded && step === 'source' && (
            <div className="studio-empty" role="status" aria-label="载入剧本">
              {writing.status === 'loading' ? (
                <Skeleton active title={false} paragraph={{ rows: 6 }} style={{ maxWidth: 728 }} />
              ) : (
                '内容未载入，请从保存状态重试后编辑。'
              )}
            </div>
          )}
          <Suspense
            fallback={
              <div className="studio-empty" role="status">
                <Spin /> 正在载入创作阶段…
              </div>
            }
          >
            {writing.loaded && (step === 'source' || visitedStages.has('source')) && (
              <section
                hidden={step !== 'source'}
                id="creation-stage-source"
                data-testid="creation-stage-source"
                className="creation-stage"
                aria-label="剧本创作"
              >
                <SourceStage
                  value={value}
                  writing={writing}
                  readOnly={writingReadOnly}
                  onChange={update}
                  projectId={session.projectId}
                  writingSession={writing.session}
                  accountId={auth.user?.id}
                  registerBarrier={(barrier) => {
                    sourceBarrier.current = barrier;
                  }}
                />
              </section>
            )}
            {(step === 'assets' || visitedStages.has('assets')) && (
              <section
                hidden={step !== 'assets'}
                id="creation-stage-assets"
                data-testid="creation-stage-assets"
                className="creation-stage"
                aria-label="素材准备"
              >
                <AssetsStage
                  refreshToken={assistantRevision}
                  value={workflow}
                  readOnly={readOnly}
                  projectId={session.projectId}
                  onChange={update}
                  onApply={update}
                  writingSession={writing.session}
                  onConfirmScript={() => goToStep('source')}
                  registerBarrier={(barrier) => {
                    extractionBarrier.current = barrier;
                  }}
                />
              </section>
            )}
            {step === 'storyboard' && (
              <section
                id="creation-stage-storyboard"
                data-testid="creation-stage-storyboard"
                className="creation-stage"
                aria-label="分镜制作"
              >
                <StoryboardStage
                  refreshToken={assistantRevision}
                  value={workflow}
                  readOnly={writingReadOnly}
                  onChange={update}
                  projectId={session.projectId}
                  contentVersion={writing.contentVersion}
                  scriptId={writing.scriptId}
                  confirmed={writing.confirmed}
                  writingSession={writing.session}
                  sourceMedia={parameters.get('source_media') === 'video' ? 'video' : undefined}
                  registerBarrier={(barrier) => {
                    storyboardBarrier.current = barrier;
                  }}
                />
              </section>
            )}
            {step === 'assembly' && (
              <section id="creation-stage-assembly" className="creation-stage">
                <AssemblyStage
                  projectId={session.projectId}
                  readOnly={readOnly}
                  registerBarrier={(barrier) => {
                    assemblyBarrier.current = barrier;
                  }}
                  onStoryboard={() => goToStep('storyboard')}
                />
              </section>
            )}
          </Suspense>
        </CreationWorkspace>
        {parameters.get('source_image_group') && selectedObjectId && (step === 'assets' || step === 'storyboard') && (
          <Suspense fallback={<Spin />}>
            <ImageGroupSource
              groupId={parameters.get('source_image_group')!}
              slotId={parameters.get('source_image_slot')}
              targetKind={step === 'assets' ? 'asset' : 'shot'}
              targetId={selectedObjectId}
              onClose={() =>
                updateParameters((previous) => {
                  previous.delete('source_image_group');
                  previous.delete('source_image_slot');
                  return previous;
                })
              }
            />
          </Suspense>
        )}
      </div>
    </div>
  );
}
