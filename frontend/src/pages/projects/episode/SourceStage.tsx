import { useRef, useState } from 'react';
import { Alert, Button, Tabs } from 'antd';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import { EpisodeVisualSettings } from '../../../features/projects/EpisodeVisualSettings';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { NovelScriptGeneration } from '../../../features/projects/NovelScriptGeneration';
import type { WritingSession, WritingSnapshot } from '../../../features/projects/writing-session';
import { decodeNovelFile } from '../../../features/projects/novel-import';
import { ScriptStage } from './ScriptStage';

export function SourceStage({ value, writing, readOnly, onChange, projectId, episodeId, writingSession, tab, onTab, onContinue }: {
  value: EpisodeWorkflow; writing: WritingSnapshot; readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId: string; episodeId: string; writingSession: WritingSession;
  tab: string; onTab: (tab: string) => void; onContinue: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  async function importFile(file?: File) {
    if (!file || readOnly) return;
    setError(''); setImporting(true);
    try {
      if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 .txt 文件。');
      const content = decodeNovelFile(await file.arrayBuffer());
      if (writingSession.getSnapshot().novel.trim() && !window.confirm('导入将替换本集小说正文并自动保存，剧本不受影响。继续导入？')) return;
      writingSession.edit('novel', content);
      await writingSession.flush();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '文件读取失败，请重新选择。'); }
    finally { setImporting(false); if (input.current) input.current.value = ''; }
  }
  return <>
    <div className="episode-stage-heading"><div><h2>小说改编</h2><p>从原文到定稿，在同一个工作区完成。</p></div></div>
    <div className="writing-layout creation-workspace">
      <NovelScriptGeneration projectId={projectId} episodeId={episodeId} contentVersion={writing.contentVersion} modelId={value.models.script} novel={writing.novel} disabled={readOnly || importing} session={writingSession} onEditScript={() => onTab('script')}
        settings={<EpisodeVisualSettings projectId={projectId} episodeId={episodeId} value={value} disabled={readOnly} onChange={onChange}/>}
        modelSelector={<EpisodeModelSelect kind="text" label="剧本生成模型" value={value.models.script} disabled={readOnly} onChange={id => onChange({ ...value, models: { ...value.models, script: id } })}/>} />
      <div className="writing-paper creation-editor">
        <Tabs activeKey={tab} onChange={onTab} items={[
          { key: 'novel', label: '本集小说', children: <>
            <div className="writing-paper-heading"><span>{writing.novel.length.toLocaleString()} 字</span><Button disabled={readOnly || writing.busy} loading={importing} onClick={() => input.current?.click()}>导入 TXT</Button><input hidden ref={input} type="file" accept=".txt,text/plain" aria-label="导入小说文件" onChange={event => void importFile(event.target.files?.[0])}/></div>
            {error && <Alert type="error" showIcon message={error}/>}
            <label className="episode-editor-label"><span className="sr-only">本集小说正文</span><textarea id="episode-novel" className="episode-novel-text" rows={16} value={writing.novel} readOnly={readOnly || importing} placeholder="粘贴、导入或写下本集故事…" onChange={event => writingSession.edit('novel', event.target.value)} /></label>
            <p className="writing-paper-note">停顿 1 秒自动保存。小说与剧本分别保留。</p>
          </> },
          { key: 'script', label: '剧本定稿', children: <ScriptStage value={value} script={writing.script} confirmed={writing.confirmed} busy={writing.busy} readOnly={readOnly} onEdit={content => writingSession.edit('script', content)} onConfirm={() => void writingSession.confirm()} onContinue={onContinue}/> },
        ]}/>
      </div>
    </div>
  </>;
}
