import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { Alert, Button, Spin } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { episodePath } from '../../app/paths';
import type { ProjectSession } from '../../types/projects';
import type { RemoteEpisode } from '../../api/modules/projects';
import { readWorkflow, saveWorkflow, nearestPendingStage, type EpisodeWorkflow, type StageId } from '../../features/projects/episode-workflow';
import { episodeStages, StageNav, visibleEpisodeStage } from './episode/StageNav';
import { SourceStage } from './episode/SourceStage';
import { AssetsStage } from './episode/AssetsStage';
import { StoryboardStage } from './episode/StoryboardStage';
import { useEpisodeWriting } from '../../features/projects/useEpisodeWriting';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';
import { projectWritingWorkflow, retainLocalWorkflow } from '../../features/projects/writing-workflow';
import { hasUnsettledWriting } from '../../features/projects/writing-navigation';
import type { NavigationBarrier } from '../../features/projects/writing-navigation';

export function EpisodePage(props: ComponentProps<typeof EpisodeWorkspace>) {
  return <EpisodeWorkspace key={`${props.session.projectId}:${props.episode.id}:${props.session.mode}`} {...props} />;
}
function EpisodeWorkspace({ session, episode, number, ready, onBack }: { session: ProjectSession; episode: RemoteEpisode; number: number; ready: boolean; onBack: () => void }) {
  const [value, setValue] = useState(() => ({ ...readWorkflow(session.projectId, episode.id, { aspect: episode.aspect, style: episode.style }), aspect: episode.aspect, style: episode.style }));
  const storyboardBarrier = useRef<NavigationBarrier | null>(null);
  const extractionBarrier = useRef<NavigationBarrier | null>(null);
  const getStoryboardBarrier = useRef(() => ({
    hasUnsettled: () => !!storyboardBarrier.current?.hasUnsettled() || !!extractionBarrier.current?.hasUnsettled(),
    flush: async () => {
      for (const barrier of [storyboardBarrier.current, extractionBarrier.current]) {
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
  const [writingTab, setWritingTab] = useState(stage === 'script' ? 'script' : 'novel');
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const step = stage === 'script' ? 'source' : episodeStages.find((item) => item.id === stage)?.id ?? visibleEpisodeStage(nearestPendingStage(workflow));
  useEffect(() => {
    if (stage !== step) navigate(episodePath(session.projectId, episode.id, step), { replace: true });
  }, [stage, step, session.projectId, episode.id, navigate]);
  const latest = useRef(value);
  const [localError, setLocalError] = useState('');
  const readOnly = session.mode === 'read';
  const writingReadOnly = readOnly || !writing.loaded || writing.status === 'loading';
  const writingLabel = { loading: '正在载入服务端内容…', saved: '正文已保存', unsaved: '有未保存的修改', saving: '正在保存…', error: '保存已暂停', conflict: '版本冲突，保存已暂停' }[writing.status];
  const current = episodeStages.findIndex((stage) => stage.id === step);
  function goToStep(next: StageId) {
    if (next === 'script') setWritingTab('script');
    navigate(episodePath(session.projectId, episode.id, visibleEpisodeStage(next)));
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  function update(change: EpisodeWorkflow | ((v: EpisodeWorkflow) => EpisodeWorkflow)) {
    if (readOnly) return;
    const changed = typeof change === 'function' ? change(projectWritingWorkflow(latest.current, writing)) : change;
    const next = retainLocalWorkflow(latest.current, changed);
    latest.current = next; setValue(next);
    const result = saveWorkflow(session.projectId, episode.id, next);
    setLocalError(result.ok ? '' : result.error);
  }
  function exportDraft() {
    const url = URL.createObjectURL(new Blob([`本集小说\n${writing.novel}\n\n本集剧本\n${writing.script}`], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `episode-${episode.id}-draft.txt`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function reloadWriting() {
    if (hasUnsettledWriting(writing) && !window.confirm('载入服务端版本会替换当前草稿，尚未核实的保存结果也将以服务端为准。请先下载草稿备份。确定继续？')) return;
    void writing.session.load();
  }
  function importLegacy(field: 'novel' | 'script') {
    if (!window.confirm('将用浏览器旧稿替换当前编辑内容，并自动保存至服务端。确定导入？')) return;
    writing.session.edit(field, legacy[field]); setShowLegacy(false);
  }
  return <div className="episode-page web-episode">
    <header className="episode-top"><Button type="link" className="detail-back" icon={<Icon name="back" size={16}/>} onClick={onBack}>返回项目详情</Button><div><span>{session.project.name} / 第 {number} 集</span><h1>{episode.title}</h1></div><span className={`episode-save-state state-${writing.status}`} title="小说与剧本的自动保存状态" role={writing.message ? 'alert' : 'status'}>{writingLabel}</span></header>
    {readOnly && <p className="episode-readonly-notice">当前为只读模式，可查看本集内容。</p>}
    <div className={`episode-layout${collapsed ? ' is-collapsed' : ''}`}>
      <aside className="episode-sidebar"><div className="episode-sidebar-inner"><div className="episode-nav-heading"><p className="episode-nav-title">创作流程</p><Button type="text" aria-label={collapsed ? '展开创作流程' : '收起创作流程'} aria-expanded={!collapsed} icon={<Icon name={collapsed ? 'arrow' : 'back'} size={18}/>} onClick={() => setCollapsed(!collapsed)}/></div><StageNav active={step} onSelect={goToStep}/><div className="episode-context-summary"><strong>{value.aspect} 画幅</strong><span>{value.style || '未设置视觉风格'}</span><p>正文自动保存。生成后先预览，再选择采用。</p></div></div></aside>
      <div className="episode-content">
        {localError && <Alert type="error" showIcon message={localError}/>}

        {writing.message && <Alert type={writing.status === 'conflict' ? 'warning' : 'error'} showIcon message={writing.message} action={<div><Button disabled={writing.busy} onClick={exportDraft}>下载当前草稿</Button>{writing.status !== 'conflict' && <Button disabled={writing.busy} onClick={() => void writing.session.retry()}>重试</Button>}<Button disabled={writing.busy} onClick={reloadWriting}>载入服务端版本</Button></div>}/>}
        {(legacy.novel || legacy.script) && <p className="episode-help">发现浏览器旧稿，不会自动上传。<Button type="link" onClick={() => setShowLegacy(true)}>预览与导入</Button></p>}
        {!writing.loaded && (step === 'source' || step === 'script') && <div className="studio-empty" role="status">{writing.status === 'loading' ? <><Spin/> 正在载入本集内容…</> : '内容未载入，请重试后编辑。'}</div>}
        {writing.loaded && <section hidden={step !== 'source'} id="episode-stage-source" data-testid="episode-stage-source" className="episode-stage" aria-label="小说与剧本创作"><SourceStage value={value} writing={writing} readOnly={writingReadOnly} onChange={update} projectId={session.projectId} episodeId={episode.id} writingSession={writing.session} tab={writingTab} onTab={setWritingTab} onContinue={() => goToStep('assets')}/></section>}
        <section hidden={step !== 'assets'} id="episode-stage-assets" data-testid="episode-stage-assets" className="episode-stage" aria-label="素材准备"><AssetsStage value={workflow} readOnly={readOnly} ready={ready} projectId={session.projectId} episodeId={episode.id} onChange={update} onApply={update} writingSession={writing.session} onConfirmScript={() => goToStep('script')} registerBarrier={barrier => { extractionBarrier.current = barrier; }}/></section>
        {step === 'storyboard' && <section id="episode-stage-storyboard" data-testid="episode-stage-storyboard" className="episode-stage" aria-label="分镜制作"><StoryboardStage value={workflow} readOnly={writingReadOnly} onChange={update} projectId={session.projectId} episodeId={episode.id} contentVersion={writing.contentVersion} scriptId={writing.scriptId} confirmed={writing.confirmed} writingSession={writing.session} registerBarrier={(barrier) => { storyboardBarrier.current = barrier; }}/></section>}
        <footer className="episode-step-footer">{current > 0 && <Button className="episode-step-previous" onClick={() => goToStep(episodeStages[current - 1].id)}>上一步</Button>}<span>步骤 {current + 1} / {episodeStages.length}</span>{current < episodeStages.length - 1 && <Button className="episode-step-next" onClick={() => goToStep(episodeStages[current + 1].id)}>下一步：{episodeStages[current + 1].label}</Button>}</footer>
      </div>
    </div>
    {showLegacy && <Dialog title="浏览器旧稿预览" onClose={() => setShowLegacy(false)}><p>以下为旧版浏览器草稿，可能包含演示内容。请核对后分别导入；旧稿仍保留在本浏览器。</p>{(['novel', 'script'] as const).filter(field => legacy[field]).map(field => <div key={field}><h3>{field === 'novel' ? '小说旧稿' : '剧本旧稿'}</h3><textarea rows={8} value={legacy[field]} readOnly aria-label={field === 'novel' ? '小说旧稿预览' : '剧本旧稿预览'} style={{ width: '100%' }}/><Button disabled={writingReadOnly || writing.busy || writing.status === 'conflict' || writing.status === 'error'} onClick={() => importLegacy(field)}>导入{field === 'novel' ? '小说' : '剧本'}并自动保存</Button></div>)}</Dialog>}
  </div>;
}
