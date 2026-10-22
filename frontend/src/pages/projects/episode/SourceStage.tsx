import { useEffect, useRef, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { StudioFeedback } from '../../../components/ui/StudioFeedback';
import { PanelRight, Upload, X } from 'lucide-react';
import { useEpisodeCreationControls } from '../../../features/projects/EpisodeCreationWorkspace';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { NovelScriptGeneration } from '../../../features/projects/NovelScriptGeneration';
import type { WritingSession, WritingSnapshot } from '../../../features/projects/writing-session';
import { decodeNovelFile } from '../../../features/projects/novel-import';
import { ScriptStage } from './ScriptStage';
import { Dialog } from '../../../components/ui/Dialog';
import { useCreationDraft } from '../../../features/projects/useCreationDraft';
import { creationScope, saveRecovery } from '../../../features/projects/creation-recovery';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import '../../../features/projects/novel-editor.css';

export function SourceStage({ value, writing, readOnly, onChange, projectId, writingSession, tab, onTab, onContinue, registerBarrier }: {
  value: EpisodeWorkflow; writing: WritingSnapshot; readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId: string; writingSession: WritingSession;
  tab: string; onTab: (tab: string) => void; onContinue: () => void;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [modelFeedbackContainer, setModelFeedbackContainer] = useState<HTMLDivElement | null>(null);
  const controls = useEpisodeCreationControls();
  const draft = useCreationDraft({ projectId, objectId: projectId, purpose: 'novel_adaptation', sourceVersion: writing.contentVersion,
    initialValue: { model_config_id: /^[0-9]+$/.test(value.models.script) ? value.models.script : null }, readOnly, registerBarrier });
  useEffect(() => { if (sourceOpen && window.innerWidth <= 700) controls.setPanelOpen?.(false); }, [sourceOpen]);
  async function importFile(file?: File) {
    if (!file || readOnly || importing) return;
    setError(''); setImporting(true);
    try {
      if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 .txt 文件。');
      const content = decodeNovelFile(await file.arrayBuffer());
      const current = writingSession.getSnapshot();
      if (current.novel.trim()) saveRecovery(creationScope(`writing-before-replace:${projectId}`), current);
      writingSession.edit('novel', content);
      if (!await writingSession.flush()) throw new Error('导入内容已保留，自动保存未完成，请核对保存状态后重试。');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '文件读取失败，请重新选择。'); }
    finally { setImporting(false); if (input.current) input.current.value = ''; }
  }
  function focusSettingsToggle() {
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.novel-script-heading [aria-controls="novel-adaptation-settings"]')?.focus());
  }
  function closeSource() {
    setSourceOpen(false);
    if (window.innerWidth <= 700) focusSettingsToggle();
    else requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.novel-adaptation-settings [aria-label="查看原文"]')?.focus());
  }
  const settingsToggle = <Tooltip title={controls.panelOpen ? '收起设置' : '展开设置'}><Button type="text" size="small" aria-label={controls.panelOpen ? '收起设置' : '展开设置'} aria-expanded={!!controls.panelOpen} aria-controls="novel-adaptation-settings" icon={<PanelRight size={18}/>} onClick={() => controls.setPanelOpen?.(!controls.panelOpen)}/></Tooltip>;
  return <div className="novel-source-stage" data-legacy-tab={tab}>
    <NovelScriptGeneration projectId={projectId} contentVersion={writing.contentVersion} novel={writing.novel} disabled={readOnly || importing} session={writingSession} draft={draft} onEditScript={() => onTab('script')}
      feedbackRef={setModelFeedbackContainer}
      onViewSource={() => setSourceOpen(true)}
      settingsActions={<>
        <Tooltip title="收起剧本设置" styles={{ root: { pointerEvents: 'none' } }}><Button type="text" size="small" aria-label="收起剧本设置" icon={<X size={18}/>} onClick={() => { controls.setPanelOpen?.(false); focusSettingsToggle(); }}/></Tooltip>
      </>}
      modelSelector={<div className="novel-text-model"><EpisodeModelSelect kind="text" label="文本模型" optionLayout="compact" presentation="standard-creation" feedbackContainer={modelFeedbackContainer} value={draft.value.model_config_id ?? ''} disabled={readOnly || !draft.loaded} onChange={id => draft.edit({ model_config_id: id || null })} onResolvedChange={id => { if (draft.loaded && !draft.value.model_config_id && id) draft.edit({ model_config_id: id }); }}/></div>}>
      {generation => <ScriptStage value={value} script={writing.script} editorDocument={writing.editorDocument} confirmed={writing.confirmed} busy={writing.busy || generation.busy} readOnly={readOnly} onEdit={(content, document) => writingSession.edit('script', content, document)} onConfirm={() => void writingSession.confirm()} onContinue={onContinue}
        actions={<>{generation.history}{settingsToggle}</>}
        candidate={generation.candidate} showingCandidate={generation.showingCandidate} onShowCandidate={generation.showCandidate} onAdopt={generation.adopt} message={generation.feedback}/>}
    </NovelScriptGeneration>
    <Dialog title="当前作品小说原文" className="standard-creation-dialog novel-source-drawer" open={sourceOpen} canClose={!importing} onClose={closeSource}>
      <div className="novel-source-drawer-tools"><span>{writing.novel.length.toLocaleString()} 字</span><Button disabled={readOnly || writing.busy || importing} loading={importing} icon={<Upload size={15}/>} onClick={() => input.current?.click()}>导入小说</Button><input hidden ref={input} disabled={readOnly || writing.busy || importing} type="file" accept=".txt,text/plain" aria-label="导入小说文件" onChange={event => void importFile(event.target.files?.[0])}/></div>
      {error && <StudioFeedback type="error" title="导入小说" message={error}/>}
      <label className="novel-source-input"><span className="sr-only">当前作品小说正文</span><textarea id="episode-novel" className="episode-novel-text" rows={16} value={writing.novel} readOnly={readOnly || importing} placeholder="粘贴、导入或写下当前作品故事…" onChange={event => writingSession.edit('novel', event.target.value)}/></label>
      <footer><Button onClick={closeSource} disabled={importing}>返回剧本</Button></footer>
    </Dialog>
  </div>;
}
