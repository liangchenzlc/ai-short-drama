import { confirmAction } from '../../../components/ui/confirm';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Tooltip } from 'antd';
import { BookOpen, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useEpisodeCreationControls } from '../../../features/projects/EpisodeCreationWorkspace';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import { EpisodeVisualSettings } from '../../../features/projects/EpisodeVisualSettings';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { NovelScriptGeneration } from '../../../features/projects/NovelScriptGeneration';
import type { WritingSession, WritingSnapshot } from '../../../features/projects/writing-session';
import { decodeNovelFile } from '../../../features/projects/novel-import';
import { ScriptStage } from './ScriptStage';
import { Dialog } from '../../../components/ui/Dialog';
import { useCreationDraft } from '../../../features/projects/useCreationDraft';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import '../../../features/projects/novel-editor.css';

export function SourceStage({ value, writing, readOnly, onChange, projectId, episodeId, writingSession, tab, onTab, onContinue, registerBarrier }: {
  value: EpisodeWorkflow; writing: WritingSnapshot; readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId: string; episodeId: string; writingSession: WritingSession;
  tab: string; onTab: (tab: string) => void; onContinue: () => void;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const controls = useEpisodeCreationControls();
  const draft = useCreationDraft({ projectId, episodeId, objectId: episodeId, purpose: 'novel_adaptation', sourceVersion: writing.contentVersion,
    initialValue: { model_config_id: /^[0-9]+$/.test(value.models.script) ? value.models.script : null }, readOnly, registerBarrier });
  useEffect(() => { if (sourceOpen && window.innerWidth <= 700) controls.setPanelOpen?.(false); }, [sourceOpen]);
  async function importFile(file?: File) {
    if (!file || readOnly) return;
    setError(''); setImporting(true);
    try {
      if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 .txt 文件。');
      const content = decodeNovelFile(await file.arrayBuffer());
      if (writingSession.getSnapshot().novel.trim() && !await confirmAction('导入将替换本集小说正文并自动保存，剧本不受影响。继续导入？')) return;
      writingSession.edit('novel', content);
      if (!await writingSession.flush()) throw new Error('导入内容已保留，自动保存未完成，请核对保存状态后重试。');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '文件读取失败，请重新选择。'); }
    finally { setImporting(false); if (input.current) input.current.value = ''; }
  }
  const settingsToggle = <Tooltip title={controls.panelOpen ? '收起设置' : '展开设置'}><Button type="text" size="small" aria-label={controls.panelOpen ? '收起设置' : '展开设置'} aria-expanded={!!controls.panelOpen} aria-controls="novel-adaptation-settings" icon={controls.panelOpen ? <PanelLeftClose size={17}/> : <PanelLeftOpen size={17}/>} onClick={() => controls.setPanelOpen?.(!controls.panelOpen)}/></Tooltip>;
  return <div className="novel-source-stage" data-legacy-tab={tab}>
    <NovelScriptGeneration projectId={projectId} episodeId={episodeId} contentVersion={writing.contentVersion} novel={writing.novel} disabled={readOnly || importing} session={writingSession} draft={draft} onEditScript={() => onTab('script')}
      settingsActions={settingsToggle}
      settings={<EpisodeVisualSettings projectId={projectId} episodeId={episodeId} value={value} disabled={readOnly} onChange={onChange}/>}
      modelSelector={<EpisodeModelSelect kind="text" label="文本模型" value={draft.value.model_config_id ?? ''} disabled={readOnly || !draft.loaded} onChange={id => draft.edit({ model_config_id: id || null })} onResolvedChange={id => { if (draft.loaded && !draft.value.model_config_id && id) draft.edit({ model_config_id: id }); }}/>}>
      {generation => <ScriptStage value={value} script={writing.script} editorDocument={writing.editorDocument} confirmed={writing.confirmed} busy={writing.busy || generation.busy} readOnly={readOnly} onEdit={(content, document) => writingSession.edit('script', content, document)} onConfirm={() => void writingSession.confirm()} onContinue={onContinue}
        actions={<><Button type="text" size="small" icon={<BookOpen size={15}/>} onClick={() => setSourceOpen(true)}>查看原文</Button>{generation.history}{settingsToggle}</>}
        candidate={generation.candidate} showingCandidate={generation.showingCandidate} onShowCandidate={generation.showCandidate} onAdopt={generation.adopt} message={generation.feedback}/>}
    </NovelScriptGeneration>
    <Dialog title="本集小说原文" className="novel-source-drawer" open={sourceOpen} canClose={!importing} onClose={() => setSourceOpen(false)}>
      <div className="novel-source-drawer-tools"><span>{writing.novel.length.toLocaleString()} 字</span><Button disabled={readOnly || writing.busy} loading={importing} onClick={() => input.current?.click()}>导入 TXT</Button><input hidden ref={input} type="file" accept=".txt,text/plain" aria-label="导入小说文件" onChange={event => void importFile(event.target.files?.[0])}/></div>
      {error && <Alert type="error" showIcon message={error}/>}
      <label className="novel-source-input"><span className="sr-only">本集小说正文</span><textarea id="episode-novel" className="episode-novel-text" rows={16} value={writing.novel} readOnly={readOnly || importing} placeholder="粘贴、导入或写下本集故事…" onChange={event => writingSession.edit('novel', event.target.value)}/></label>
      <footer><span>停顿 1 秒自动保存</span><Button onClick={() => setSourceOpen(false)} disabled={importing}>返回剧本</Button></footer>
    </Dialog>
  </div>;
}
