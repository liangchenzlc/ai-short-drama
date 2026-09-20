import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { Button } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { episodePath } from '../../app/paths';
import type { ProjectSession } from '../../types/projects';
import type { RemoteEpisode } from '../../api/modules/projects';
import { readWorkflow, saveWorkflow, nearestPendingStage, type EpisodeWorkflow, type StageId } from '../../features/projects/episode-workflow';
import { episodeStages, StageNav, visibleEpisodeStage } from './episode/StageNav';
import { SourceStage } from './episode/SourceStage';
import { ScriptStage } from './episode/ScriptStage';
import { AssetsStage } from './episode/AssetsStage';
import { StoryboardStage } from './episode/StoryboardStage';
import { listUsableMedia, type ListedMedia } from '../../features/projects/episode-media';

export function EpisodePage(props: ComponentProps<typeof EpisodeWorkspace>) {
  return <EpisodeWorkspace key={`${props.session.projectId}:${props.episode.id}:${props.session.mode}`} {...props} />;
}
function EpisodeWorkspace({ session, episode, number, ready, onBack }: { session: ProjectSession; episode: RemoteEpisode; number: number; ready: boolean; onBack: () => void }) {
  const [value, setValue] = useState(() => ({ ...readWorkflow(session.projectId, episode.id, { aspect: episode.aspect, style: episode.style }), aspect: episode.aspect, style: episode.style }));
  const { stage } = useParams();
  const navigate = useNavigate();
  const step = episodeStages.find((item) => item.id === stage)?.id ?? visibleEpisodeStage(nearestPendingStage(value));
  useEffect(() => {
    if (stage !== step) navigate(episodePath(session.projectId, episode.id, step), { replace: true });
  }, [stage, step, session.projectId, episode.id, navigate]);
  const latest = useRef(value);
  const [images, setImages] = useState<readonly ListedMedia[]>([]);
  const [saveMessage, setSaveMessage] = useState('内容已载入');
  const [saveError, setSaveError] = useState('');
  const readOnly = session.mode === 'read';
  const current = episodeStages.findIndex((stage) => stage.id === step);
  useEffect(() => {
    let active = true;
    if (!ready) { setImages([]); return; }
    void Promise.all([listUsableMedia(session.projectId, 'image/'), listUsableMedia(session.projectId, 'video/')]).then(([a, b]) => { if (active) setImages([...a, ...b]); });
    return () => { active = false; };
  }, [session.projectId, ready, value.assets, value.shots]);
  function goToStep(next: StageId) {
    navigate(episodePath(session.projectId, episode.id, visibleEpisodeStage(next)));
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  function update(change: EpisodeWorkflow | ((v: EpisodeWorkflow) => EpisodeWorkflow)) {
    if (readOnly) return;
    const next = typeof change === 'function' ? change(latest.current) : change;
    latest.current = next; setValue(next);
    const result = saveWorkflow(session.projectId, episode.id, next);
    setSaveMessage(result.ok ? '制作内容已保存至本浏览器' : '未保存');
    setSaveError(result.ok ? '' : result.error);
  }
  return <div className="episode-page web-episode">
    <header className="episode-top"><Button type="link" className="detail-back" onClick={onBack}>← 返回项目详情</Button><div><span>{session.project.name} / 第 {number} 集</span><h1>{episode.title}</h1></div><span className={saveError ? 'episode-save-error' : 'episode-top-status'} role={saveError ? 'alert' : 'status'}>{saveError || saveMessage}</span></header>
    {readOnly && <p className="episode-readonly-notice">当前为只读模式，可查看本集内容。</p>}
    <div className="episode-layout">
      <aside className="episode-sidebar"><div className="episode-sidebar-inner"><p>本集制作</p><StageNav active={step} onSelect={goToStep}/><div className="episode-context-summary"><strong>{value.aspect} 画幅</strong><span>{value.assets.length} 项素材 · {value.shots.length} 个分镜</span><p>编辑自动保存，可随时切换步骤。</p></div></div></aside>
      <div className="episode-content">
        <div className="episode-demo-banner" role="note"><span>制作演示 · 下方内容仅保存在本浏览器，生成操作不调用 AI 服务。</span><span>{current + 1} / {episodeStages.length}</span></div>
        <section hidden={step !== 'source'} id="episode-stage-source" data-testid="episode-stage-source" className="episode-stage" aria-label="小说与剧本生成"><SourceStage value={value} readOnly={readOnly} onChange={update}/></section>
        <section hidden={step !== 'script'} id="episode-stage-script" data-testid="episode-stage-script" className="episode-stage" aria-label="剧本确认与素材拆解"><ScriptStage value={value} readOnly={readOnly} visualReadOnly onChange={update} projectAspect={session.project.aspect}/></section>
        <section hidden={step !== 'assets'} id="episode-stage-assets" data-testid="episode-stage-assets" className="episode-stage" aria-label="素材图片"><AssetsStage value={value} readOnly={readOnly} ready={ready} projectId={session.projectId} onChange={update} onApply={update}/></section>
        <section hidden={step !== 'storyboard'} id="episode-stage-storyboard" data-testid="episode-stage-storyboard" className="episode-stage" aria-label="分镜制作"><StoryboardStage value={value} readOnly={readOnly} onChange={update} projectId={session.projectId} mediaItems={images}/></section>
        <footer className="episode-step-footer">{current > 0 && <Button className="episode-step-previous" onClick={() => goToStep(episodeStages[current - 1].id)}>上一步</Button>}<span>{episodeStages[current].label}</span>{current < episodeStages.length - 1 && <Button className="episode-step-next" onClick={() => goToStep(episodeStages[current + 1].id)}>下一步：{episodeStages[current + 1].label}</Button>}</footer>
      </div>
    </div>
  </div>;
}
