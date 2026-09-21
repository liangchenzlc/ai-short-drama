import { Button } from 'antd';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { NovelScriptGeneration } from '../../../features/projects/NovelScriptGeneration';
import type { WritingSession } from '../../../features/projects/writing-session';

export function SourceStage({ value, novel, readOnly, onChange, onEdit, projectId, episodeId, contentVersion, writingSession, onWriteScript }: {
  value: EpisodeWorkflow; novel: string; readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void; onEdit: (content: string) => void;
  projectId: string; episodeId: string; contentVersion: string; writingSession: WritingSession;
  onWriteScript: () => void;
}) {
  return <>
    <div className="episode-stage-heading"><div><h2>把故事写成剧本</h2><p>放入本集原文，让 AI 协助改编；也可以直接编写剧本。</p></div><Button onClick={onWriteScript}>直接写剧本</Button></div>
    <div className="writing-layout">
      <div className="writing-paper">
        <div className="writing-paper-heading"><label htmlFor="episode-novel">本集小说</label><span>{novel.length.toLocaleString()} 字</span></div>
        <label className="episode-editor-label"><span className="sr-only">本集小说正文</span><textarea id="episode-novel" className="episode-novel-text" rows={16} value={novel} readOnly={readOnly} placeholder="粘贴或写下本集的故事原文…" onChange={event => onEdit(event.target.value)} /></label>
        <p className="writing-paper-note">停顿 1 秒自动保存。原文与剧本分别保留。</p>
      </div>
      <NovelScriptGeneration projectId={projectId} episodeId={episodeId} contentVersion={contentVersion} modelId={value.models.script} novel={novel} disabled={readOnly} session={writingSession} onEditScript={onWriteScript}
        modelSelector={<EpisodeModelSelect kind="text" label="剧本生成模型" value={value.models.script} disabled={readOnly} onChange={id => onChange({ ...value, models: { ...value.models, script: id } })}/>} />
    </div>
  </>;
}
