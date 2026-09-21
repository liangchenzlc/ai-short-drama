import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { NovelScriptGeneration } from '../../../features/projects/NovelScriptGeneration';
import type { WritingSession } from '../../../features/projects/writing-session';

export function SourceStage({ value, novel, readOnly, onChange, onEdit, projectId, episodeId, contentVersion, writingSession }: {
  value: EpisodeWorkflow; novel: string; readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void; onEdit: (content: string) => void;
  projectId: string; episodeId: string; contentVersion: string; writingSession: WritingSession;
}) {
  return <>
    <div className="episode-stage-heading"><div><h2>小说与剧本生成</h2><p>小说与剧本独立保存至服务端。编辑后停顿 1 秒自动保存。</p></div><span>{novel.length} 字</span></div>
    <label className="episode-editor-label">本集小说<textarea className="episode-novel-text" rows={12} value={novel} readOnly={readOnly} placeholder="粘贴本集范围内的小说原文" onChange={event => onEdit(event.target.value)} /></label>
    <div className="episode-stage-controls episode-source-controls"><label>剧本生成模型<EpisodeModelSelect kind="text" label="剧本生成模型" value={value.models.script} disabled={readOnly} onChange={id => onChange({ ...value, models: { ...value.models, script: id } })}/></label></div>
    <NovelScriptGeneration projectId={projectId} episodeId={episodeId} contentVersion={contentVersion} modelId={value.models.script} novel={novel} disabled={readOnly} session={writingSession}/>
  </>;
}
