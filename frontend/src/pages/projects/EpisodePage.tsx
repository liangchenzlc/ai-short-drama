import { confirmAction } from '../../components/ui/confirm';
import { useCallback, useEffect, useRef, useState, type ComponentProps } from 'react';
import { Alert, Button, Spin } from 'antd';
import { lazy, Suspense } from 'react';
import { preloadable } from '../../components/ui/preloadable';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { episodePath } from '../../app/paths';
import type { ProjectSession } from '../../types/projects';
import type { RemoteEpisode } from '../../api/modules/projects';
import { readWorkflow, saveWorkflow, nearestPendingStage, type EpisodeWorkflow, type EpisodeNavigationStage as StageId } from '../../features/projects/episode-workflow';
const loadAssemblyStage = () => import('./episode/AssemblyStage');
const loadSourceStage = () => import('./episode/SourceStage');
const loadAssetsStage = () => import('./episode/AssetsStage');
const loadStoryboardStage = () => import('./episode/StoryboardStage');
const AssemblyStage = preloadable(() => loadAssemblyStage().then(module => ({ default: module.AssemblyStage })));
const CreativeAssistantPanel = lazy(() => import('../../features/ai-assistant/CreativeAssistantPanel').then(module => ({ default: module.CreativeAssistantPanel })));
const LegacyCreationRecord = lazy(() => import('../../features/agents/LegacyCreationRecord').then(module => ({ default: module.LegacyCreationRecord })));
const SourceStage = preloadable(() => loadSourceStage().then(module => ({ default: module.SourceStage })));
const AssetsStage = preloadable(() => loadAssetsStage().then(module => ({ default: module.AssetsStage })));
const StoryboardStage = preloadable(() => loadStoryboardStage().then(module => ({ default: module.StoryboardStage })));
const AgentArtifactShelf = lazy(() => import('../../features/agents/AgentArtifactShelf').then(module => ({ default: module.AgentArtifactShelf })));
import { episodeStages, StageNav, visibleEpisodeStage } from './episode/StageNav';
import { useEpisodeWriting } from '../../features/projects/useEpisodeWriting';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';
import { projectWritingWorkflow, retainLocalWorkflow } from '../../features/projects/writing-workflow';
import { hasUnsettledWriting } from '../../features/projects/writing-navigation';
import type { NavigationBarrier } from '../../features/projects/writing-navigation';
import { useAuth } from '../../features/auth/AuthSession';
import { AccountControls } from '../../features/auth/AccountControls';
import { accountStorage, workflowStorage } from '../../features/auth/account-storage';
import { EpisodeCreationWorkspace } from '../../features/projects/EpisodeCreationWorkspace';
import { useAgentAvailability } from '../../features/agents/useAgentAvailability';
import { withEpisodeView } from '../../features/agents/agent-navigation';
import { assistantView, replaceEpisodeView, selectAssistantConversation, selectEpisodeObject, setAssistantOpen } from '../../features/projects/assistant-navigation';
import { episodeConversationScope, type AgentSubject } from '../../features/agents/agent-scope';
import type { AgentConversation, ConversationScope } from '../../api/types/agents';
import type { AssistantMessageContext, AssistantReference } from '../../api/types/assistant';
import { agentsApi } from '../../api/modules/agents';
import { storyboardApi } from '../../api/modules/storyboard';
import { assetLibraries } from '../../api/modules/assets';
import { errorMessage } from '../../api/http';
import type { AgentArtifactDetail, AgentArtifactOpenRequest } from '../../api/types/agent-artifacts';
import '../../features/agents/agents.css';

export async function preloadEpisodeStage(stage?: string): Promise<void> {
  if (stage === 'source' || stage === 'script') await SourceStage.preload();
  else if (stage === 'assets') await AssetsStage.preload();
  else if (stage === 'storyboard' || stage === 'video') await StoryboardStage.preload();
  else if (stage === 'assembly') await AssemblyStage.preload();
}

export function EpisodePage(props: ComponentProps<typeof EpisodeWorkspace>) {
  return <EpisodeWorkspace key={`${props.session.projectId}:${props.episode.id}:${props.session.mode}`} {...props} />;
}
function EpisodeWorkspace({ session, episode, number, ready, onBack }: { session: ProjectSession; episode: RemoteEpisode; number: number; ready: boolean; onBack: () => void }) {
  const auth = useAuth();
  const storage = auth.enabled && auth.user ? accountStorage(auth.user.id) : workflowStorage();
  const [value, setValue] = useState(() => ({ ...readWorkflow(session.projectId, episode.id, { aspect: episode.aspect, style: episode.style }, storage), aspect: episode.aspect, style: episode.style }));
  const storyboardBarrier = useRef<NavigationBarrier | null>(null);
  const extractionBarrier = useRef<NavigationBarrier | null>(null);
  const assemblyBarrier = useRef<NavigationBarrier | null>(null);
  const getStoryboardBarrier = useRef(() => ({
    hasUnsettled: () => !!storyboardBarrier.current?.hasUnsettled() || !!extractionBarrier.current?.hasUnsettled() || !!assemblyBarrier.current?.hasUnsettled(),
    flush: async () => {
      for (const barrier of [storyboardBarrier.current, extractionBarrier.current, assemblyBarrier.current]) {
        if (barrier?.hasUnsettled() && !await barrier.flush()) return false;
      }
      return true;
    },
  })).current;
  const writing = useEpisodeWriting(session.projectId, episode.id, getStoryboardBarrier);
  const workflow = projectWritingWorkflow(value, writing);
  const [legacy] = useState(() => ({ novel: value.novel, script: value.scriptDraft }));
  const [showLegacy, setShowLegacy] = useState(false);
  const { stage } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const legacyNavigation = useRef({ active: true, generation: 0, accountId: auth.user?.id, locationKey: location.key });
  if (legacyNavigation.current.accountId !== auth.user?.id || legacyNavigation.current.locationKey !== location.key) {
    ++legacyNavigation.current.generation;
    legacyNavigation.current.accountId = auth.user?.id;
    legacyNavigation.current.locationKey = location.key;
  }
  useEffect(() => {
    legacyNavigation.current.active = true;
    return () => { legacyNavigation.current.active = false; ++legacyNavigation.current.generation; };
  }, []);
  const updateParameters = useCallback((update: (previous: URLSearchParams) => URLSearchParams) => {
    ++legacyNavigation.current.generation;
    replaceEpisodeView(episodePath(session.projectId, episode.id), navigate, update);
  }, [session.projectId, episode.id, navigate]);
  const parameters = assistantView(location.search);
  const step = visibleEpisodeStage(stage === 'script' ? 'source' : episodeStages.find((item) => item.id === stage)?.id ?? nearestPendingStage(workflow));
  const [visitedStages, setVisitedStages] = useState(() => new Set([step]));
  useEffect(() => { setVisitedStages(previous => previous.has(step) ? previous : new Set([...previous, step])); }, [step]);
  const assistantOpen = parameters.get('assistant') === 'open';
  const conversationId = parameters.get('conversation') ?? undefined;
  const legacyConversationId = parameters.get('legacy_conversation') ?? undefined;
  const [assistantMounted, setAssistantMounted] = useState(assistantOpen);
  useEffect(() => { if (assistantOpen) setAssistantMounted(true); }, [assistantOpen]);
  useEffect(() => {
    if (parameters.toString() !== new URLSearchParams(location.search).toString()) updateParameters(previous => assistantView(previous.toString()));
  }, [location.search, updateParameters]);
  const [agentConversation, setAgentConversation] = useState<AgentConversation | null>(null);
  const [artifactRequest, setArtifactRequest] = useState<{ accountId: string; value: AgentArtifactOpenRequest } | null>(null);
  const artifactNonce = useRef(0);
  const [artifactRevision, setArtifactRevision] = useState(0);
  const [extractionReview, setExtractionReview] = useState<{ artifact: AgentArtifactDetail; nonce: number } | null>(null);
  const availability = useAgentAvailability();
  const [writingTab, setWritingTab] = useState(stage === 'script' ? 'script' : 'novel');
  const [collapsed, setCollapsed] = useState(false);
  const [subjects, setSubjects] = useState<Record<string, AgentSubject | null>>({});
  const objectParameter = step === 'assets' ? 'asset' : step === 'storyboard' ? 'shot' : null;
  const selectedObjectId = objectParameter ? parameters.get(objectParameter) : null;
  const selectedSubject = subjects[step];
  const subject: AgentSubject | null = selectedObjectId ? selectedSubject?.id === selectedObjectId ? selectedSubject
    : { type: step === 'assets' ? 'asset' : 'shot', id: selectedObjectId, label: step === 'assets' ? '当前素材' : '当前分镜' } : null;
  const objectView = useRef({ step, updateParameters }); objectView.current = { step, updateParameters };
  const selectSubject = useCallback((next: AgentSubject | null) => {
    const { step, updateParameters } = objectView.current;
    setSubjects(previous => {
      const old = previous[step];
      return old?.id === next?.id && old?.label === next?.label && old?.revision === next?.revision ? previous : { ...previous, [step]: next };
    });
    updateParameters(previous => selectEpisodeObject(previous.toString(), step, next?.id ?? null));
  }, []);
  const closeAssistant = useCallback(() => { updateParameters(previous => setAssistantOpen(previous.toString(), false)); }, [updateParameters]);
  useEffect(() => {
    if (stage !== step) navigate(withEpisodeView(episodePath(session.projectId, episode.id, step), location.search), { replace: true });
  }, [stage, step, session.projectId, episode.id, navigate, location.search]);
  const latest = useRef(value);
  const [localError, setLocalError] = useState('');
  const readOnly = session.mode === 'read';
  const writingReadOnly = readOnly || !writing.loaded || writing.status === 'loading' || !!writing.recoverable || !!writing.recoveryBlocked;
  const writingLabel = { loading: '正在载入服务端内容…', saved: '正文已保存', unsaved: '有未保存的修改', saving: '正在保存…', error: '保存已暂停', conflict: '版本冲突，保存已暂停' }[writing.status];
  const current = episodeStages.findIndex((stage) => stage.id === step);
  function goToStep(next: StageId) {
    ++legacyNavigation.current.generation;
    if (next === 'script') setWritingTab('script');
    navigate(withEpisodeView(episodePath(session.projectId, episode.id, visibleEpisodeStage(next)), assistantView(window.location.search).toString()));
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  async function saveBeforeAgentSend() {
    if (!writing.loaded || writing.status === 'loading' || !await writing.session.flush()) return false;
    const barrier = getStoryboardBarrier();
    return !barrier.hasUnsettled() || await barrier.flush();
  }
  const assistantContext = useRef({ step, subject, writing, readOnly }); assistantContext.current = { step, subject, writing, readOnly };
  const prepareAssistantContext = useCallback(async (include: boolean, references: readonly AssistantReference[] = []): Promise<AssistantMessageContext | null> => {
    if (!include && !references.length) return null;
    const captured = assistantContext.current;
    if (!captured.writing.loaded || captured.writing.recoverable || captured.writing.recoveryBlocked || !await captured.writing.session.flush()) throw new Error('当前正文尚未保存成功，请处理保存提示后再发送。');
    const barrier = getStoryboardBarrier();
    if (barrier.hasUnsettled() && !await barrier.flush()) throw new Error('当前作品尚未保存成功，请处理编辑或冲突提示后再发送。');
    const current = assistantContext.current;
    if (current.step !== captured.step || current.subject?.id !== captured.subject?.id || hasUnsettledWriting(current.writing.session.getSnapshot()) || barrier.hasUnsettled()) throw new Error('作品或选择对象在准备期间发生变化，消息草稿已保留，请重新发送。');
    const revision = current.writing.session.getSnapshot().contentVersion;
    const context: AssistantMessageContext = { kind: 'episode', id: episode.id, revision, stage: captured.step, include_document: include };
    if (captured.step === 'storyboard' || captured.step === 'assembly') {
      const page = await storyboardApi(session.projectId, episode.id).shots();
      context.storyboard_revision = page.storyboard_version;
    }
    const selectedReferences = [...references];
    if (include && captured.subject && !selectedReferences.some(item => item.kind === captured.subject?.type && item.id === captured.subject.id)) selectedReferences.push({ kind: captured.subject.type, id: captured.subject.id });
    if (selectedReferences.length) {
      context.selected = await Promise.all(selectedReferences.map(async reference => {
        if (reference.kind === 'node') throw new Error('当前分集不能引用画布节点。');
        const selected = reference.kind === 'asset' ? await assetLibraries.detail(reference.id) : (await storyboardApi(session.projectId, episode.id).shot(reference.id)).shot;
        if (reference.revision && selected.row_version !== reference.revision) throw new Error('引用对象已更新，请重新选择引用后发送。');
        return { kind: reference.kind, id: selected.id, revision: selected.row_version };
      }));
    }
    const settled = assistantContext.current;
    if (settled.step !== captured.step || settled.subject?.id !== captured.subject?.id || hasUnsettledWriting(settled.writing.session.getSnapshot()) || barrier.hasUnsettled() || settled.writing.session.getSnapshot().contentVersion !== revision) throw new Error('作品在读取期间发生变化，消息草稿已保留，请重新发送。');
    return context;
  }, [episode.id, session.projectId, getStoryboardBarrier]);
  function openArtifact(id: string, runId?: string, privateConversationId?: string, legacyScope?: ConversationScope) {
    setArtifactRequest({ accountId: auth.user?.id ?? 'anonymous', value: { id, runId, conversationId: privateConversationId, scope: legacyConversationId ? legacyScope : episodeConversationScope('source', episode.id), nonce: ++artifactNonce.current } });
  }
  async function openLegacyRecord(id: string) {
    const generation = ++legacyNavigation.current.generation;
    const accountId = auth.user?.id;
    const locationKey = location.key;
    const href = window.location.href;
    const isCurrent = () => legacyNavigation.current.active
      && legacyNavigation.current.generation === generation
      && legacyNavigation.current.accountId === accountId
      && legacyNavigation.current.locationKey === locationKey
      && window.location.href === href;
    try {
      const item = await agentsApi.conversation(id);
      if (!isCurrent()) return;
      if (item.project_id !== session.projectId || !item.episode_id || item.scope_version === 2) throw new Error('这段历史记录不属于当前项目。');
      const next = setAssistantOpen(location.search, true); next.set('legacy_conversation', id);
      navigate(withEpisodeView(episodePath(session.projectId, item.episode_id, item.stage ?? 'source'), next.toString()));
    } catch (cause) { if (isCurrent()) setLocalError(errorMessage(cause)); }
  }
  async function artifactApplied() {
    if (hasUnsettledWriting(writing.session.getSnapshot()) || getStoryboardBarrier().hasUnsettled()) return false;
    if (!await writing.session.load()) return false;
    if (getStoryboardBarrier().hasUnsettled()) return false;
    setArtifactRevision(value => value + 1); return true;
  }
  function update(change: EpisodeWorkflow | ((v: EpisodeWorkflow) => EpisodeWorkflow)) {
    if (readOnly) return;
    const changed = typeof change === 'function' ? change(projectWritingWorkflow(latest.current, writing)) : change;
    const next = retainLocalWorkflow(latest.current, changed);
    latest.current = next; setValue(next);
    const result = saveWorkflow(session.projectId, episode.id, next, storage);
    setLocalError(result.ok ? '' : result.error);
  }
  function exportDraft() {
    const url = URL.createObjectURL(new Blob([`本集小说\n${writing.novel}\n\n本集剧本\n${writing.script}`], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `episode-${episode.id}-draft.txt`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportRecovery() {
    try {
      const content = writing.session.exportRecovered();
      if (!content) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `episode-${episode.id}-recovery.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setLocalError('无法下载本机恢复记录，请检查浏览器存储权限后重试。'); }
  }
  async function reloadWriting() {
    if (hasUnsettledWriting(writing) && !await confirmAction('载入服务端版本会替换当前草稿，尚未核实的保存结果也将以服务端为准。请先下载草稿备份。确定继续？')) return;
    void writing.session.load();
  }
  async function importLegacy(field: 'novel' | 'script') {
    if (!await confirmAction('将用浏览器旧稿替换当前编辑内容，并自动保存至服务端。确定导入？')) return;
    writing.session.edit(field, legacy[field]); setShowLegacy(false);
  }
  return <div className="episode-page web-episode">
    <header className="episode-top"><Button type="link" className="detail-back" icon={<Icon name="back" size={16}/>} onClick={onBack}>返回项目详情</Button><div className="episode-heading"><span>{session.project.name} / 第 {number} 集</span><h1>{episode.title}</h1></div><Button className="episode-assistant-entry" aria-expanded={assistantOpen} aria-controls="agent-conversation-pane" onClick={() => updateParameters(previous => setAssistantOpen(previous.toString(), previous.get('assistant') !== 'open'))}>AI 创作助手</Button><span className={`episode-save-state state-${writing.status}`} title="小说与剧本的自动保存状态" role={writing.message ? 'alert' : 'status'}>{writingLabel}</span><AccountControls /></header>
    {readOnly && <p className="episode-readonly-notice">当前为只读模式，可查看本集内容。</p>}
    <div className={`episode-layout${collapsed ? ' is-collapsed' : ''}`}>
      <aside className="episode-sidebar"><div className="episode-sidebar-inner"><div className="episode-nav-heading"><p className="episode-nav-title">创作流程</p><Button type="text" aria-label={collapsed ? '展开创作流程' : '收起创作流程'} aria-expanded={!collapsed} icon={<Icon name={collapsed ? 'arrow' : 'back'} size={18}/>} onClick={() => setCollapsed(!collapsed)}/></div><StageNav active={step} onSelect={goToStep}/><div className="episode-context-summary"><strong>{value.aspect} 画幅</strong><span>{value.style || '未设置视觉风格'}</span><p>正文自动保存。生成后先预览，再选择采用。</p></div></div></aside>
      <div className="episode-content">
        <EpisodeCreationWorkspace stage={step} subject={subject} onSubject={selectSubject} assistantOpen={assistantOpen} onCloseAssistant={closeAssistant} workRequest={artifactRequest?.accountId === (auth.user?.id ?? 'anonymous') ? artifactRequest.value.nonce : 0} assistantPanel={assistantMounted ? <Suspense fallback={<div role="status" className="studio-empty">正在载入 AI 创作助手…</div>}>
          <div hidden={!!legacyConversationId}><CreativeAssistantPanel open={assistantOpen && !legacyConversationId} key={auth.user?.id ?? 'anonymous'} projectId={session.projectId} projectTitle={session.project.name} accountId={auth.user?.id} readOnly={readOnly} onClose={closeAssistant} selectedConversationId={conversationId} onConversationChange={id => updateParameters(previous => selectAssistantConversation(previous.toString(), id))} onOpenModelSettings={() => navigate('/ai_config')} contextPreview={{ key: `${episode.id}:${step}:${subject?.id ?? ''}`, label: `${episode.title}${subject ? ` · ${subject.label}` : ''}` }} mentionReferences={subject ? [{ kind: subject.type, id: subject.id, name: subject.label, revision: subject.revision }] : []} prepareContext={prepareAssistantContext} onOpenLegacy={id => { void openLegacyRecord(id); }}/></div>
          {legacyConversationId && <LegacyCreationRecord key={`${auth.user?.id ?? 'anonymous'}:${legacyConversationId}`} projectId={session.projectId} episodeId={episode.id} id={legacyConversationId} accountId={auth.user?.id ?? 'anonymous'} readOnly={readOnly} beforeSend={saveBeforeAgentSend} onClose={closeAssistant} onBack={() => updateParameters(previous => { const next = new URLSearchParams(previous); next.delete('legacy_conversation'); return next; })} onConversation={setAgentConversation} onOpenArtifact={openArtifact}/>}</Suspense> : null}>
        {localError && <Alert type="error" showIcon message={localError}/>}
        {(writing.recoverable || writing.recoveryBlocked) && <Alert type="warning" showIcon message={writing.recoveryBlocked ? '本机正文恢复记录无法读取' : '发现本机未保存的正文恢复稿'} description={writing.recoveryBlocked ? '请先下载完整恢复记录核对。明确放弃前不会覆盖记录或提交正文。' : writing.recoveryChanged ? '基准版本已变化，请对照当前正文；恢复前会重新读取最新版本。' : '恢复稿不会自动提交，可先下载完整内容核对再恢复。'} action={<div><Button disabled={writing.busy} onClick={exportRecovery}>下载完整恢复稿</Button>{!writing.recoveryBlocked && <Button disabled={readOnly || writing.busy} onClick={async () => { const draft = writing.session.getRecoveredDraft(); if (!draft) return; if (await confirmAction(`小说恢复稿（前 500 字）：\n${draft.novel.slice(0, 500)}\n\n剧本恢复稿（前 500 字）：\n${draft.script.slice(0, 500)}\n\n${writing.recoveryChanged ? '确认以最新服务端版本为基础保留恢复稿并继续编辑？' : '恢复到编辑器，核对后再保存？'}`)) await writing.session.restoreRecovered(!!writing.recoveryChanged); }}>核对并恢复正文</Button>}<Button disabled={writing.busy} onClick={async () => { if (await confirmAction('确定放弃本机正文恢复稿，保留当前服务端版本？')) { try { writing.session.discardRecovered(); } catch { setLocalError('无法清除本机正文恢复记录，请检查浏览器存储权限。'); } } }}>放弃恢复稿</Button></div>}/>}

        {writing.message && <Alert type={writing.status === 'conflict' ? 'warning' : 'error'} showIcon message={writing.message} action={<div><Button disabled={writing.busy} onClick={exportDraft}>下载当前草稿</Button>{writing.status !== 'conflict' && <Button disabled={writing.busy} onClick={() => void writing.session.retry()}>重试</Button>}<Button disabled={writing.busy} onClick={reloadWriting}>载入服务端版本</Button></div>}/>}
        {(legacy.novel || legacy.script) && <p className="episode-help">发现浏览器旧稿，不会自动上传。<Button type="link" onClick={() => setShowLegacy(true)}>预览与导入</Button></p>}
        {!writing.loaded && step === 'source' && <div className="studio-empty" role="status">{writing.status === 'loading' ? <><Spin/> 正在载入本集内容…</> : '内容未载入，请重试后编辑。'}</div>}
        <Suspense fallback={<div className="studio-empty" role="status"><Spin /> 正在载入创作阶段…</div>}>
        {writing.loaded && (step === 'source' || visitedStages.has('source')) && <section hidden={step !== 'source'} id="episode-stage-source" data-testid="episode-stage-source" className="episode-stage" aria-label="小说与剧本创作"><SourceStage value={value} writing={writing} readOnly={writingReadOnly} onChange={update} projectId={session.projectId} episodeId={episode.id} writingSession={writing.session} tab={writingTab} onTab={setWritingTab} onContinue={() => goToStep('assets')}/></section>}
        {(step === 'assets' || visitedStages.has('assets')) && <section hidden={step !== 'assets'} id="episode-stage-assets" data-testid="episode-stage-assets" className="episode-stage" aria-label="素材准备"><AssetsStage value={workflow} readOnly={readOnly} ready={ready} projectId={session.projectId} episodeId={episode.id} onChange={update} onApply={update} writingSession={writing.session} onConfirmScript={() => goToStep('script')} refreshToken={artifactRevision} externalReview={extractionReview} registerBarrier={barrier => { extractionBarrier.current = barrier; }}/></section>}
        {step === 'storyboard' && <section id="episode-stage-storyboard" data-testid="episode-stage-storyboard" className="episode-stage" aria-label="分镜制作"><StoryboardStage value={workflow} readOnly={writingReadOnly} onChange={update} projectId={session.projectId} episodeId={episode.id} contentVersion={writing.contentVersion} scriptId={writing.scriptId} confirmed={writing.confirmed} writingSession={writing.session} refreshToken={artifactRevision} registerBarrier={(barrier) => { storyboardBarrier.current = barrier; }}/></section>}
        {step === 'assembly' && <section id="episode-stage-assembly" className="episode-stage"><AssemblyStage projectId={session.projectId} episodeId={episode.id} readOnly={readOnly} registerBarrier={barrier => { assemblyBarrier.current = barrier; }} onStoryboard={() => goToStep('storyboard')}/></section>}
        </Suspense>
        {!availability.loading && availability.status?.schema_ready && <Suspense fallback={null}><AgentArtifactShelf dialogOnly key={auth.user?.id ?? 'anonymous'} projectId={session.projectId} episodeId={episode.id} schemaReady={!availability.loading && !!availability.status?.schema_ready} readOnly={readOnly} writingSession={writing.session} beforeAdopt={saveBeforeAgentSend} onApplied={artifactApplied}
          request={artifactRequest?.accountId === (auth.user?.id ?? 'anonymous') ? artifactRequest.value : null}
          canContinue={id => !!id && availability.available && legacyConversationId === id && agentConversation?.id === id && ['waiting_review', 'waiting_generation'].includes(agentConversation?.last_run_status ?? '')}
          onContinue={() => window.dispatchEvent(new Event('agent-run-updated'))}
          onOpenExtraction={artifact => { setExtractionReview({ artifact, nonce: ++artifactNonce.current }); goToStep('assets'); }}/></Suspense>}
        <footer className="episode-step-footer">{current > 0 && <Button className="episode-step-previous" onClick={() => goToStep(episodeStages[current - 1].id)}>上一步</Button>}<span>步骤 {current + 1} / {episodeStages.length}</span>{current < episodeStages.length - 1 && <Button className="episode-step-next" onClick={() => goToStep(episodeStages[current + 1].id)}>下一步：{episodeStages[current + 1].label}</Button>}</footer>
        </EpisodeCreationWorkspace>
      </div>
    </div>
    {showLegacy && <Dialog title="浏览器旧稿预览" onClose={() => setShowLegacy(false)}><p>以下为旧版浏览器草稿，可能包含演示内容。请核对后分别导入；旧稿仍保留在本浏览器。</p>{(['novel', 'script'] as const).filter(field => legacy[field]).map(field => <div key={field}><h3>{field === 'novel' ? '小说旧稿' : '剧本旧稿'}</h3><textarea rows={8} value={legacy[field]} readOnly aria-label={field === 'novel' ? '小说旧稿预览' : '剧本旧稿预览'} style={{ width: '100%' }}/><Button disabled={writingReadOnly || writing.busy || writing.status === 'conflict' || writing.status === 'error'} onClick={() => importLegacy(field)}>导入{field === 'novel' ? '小说' : '剧本'}并自动保存</Button></div>)}</Dialog>}
  </div>;
}
