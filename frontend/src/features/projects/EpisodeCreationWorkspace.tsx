import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AgentEpisodeLayout } from '../agents/AgentEpisodeLayout';
import type { CreationMode } from '../agents/agent-navigation';
import './episode-creation.css';

type CreationStage = 'source' | 'assets' | 'storyboard' | 'assembly';
const CreationContext = createContext<{
  target: HTMLDivElement | null;
  editorTarget: HTMLDivElement | null;
  stage: CreationStage;
  mode: CreationMode;
  revealPanel: () => void;
  setEditor: (stage: CreationStage, active: boolean) => void;
} | null>(null);

export function useEpisodeCreation() {
  return useContext(CreationContext) !== null;
}

export function useEpisodeCreationControls() {
  const workspace = useContext(CreationContext);
  return { mode: workspace?.mode ?? 'prompt', revealPanel: workspace?.revealPanel };
}

export function EpisodeCreationWorkspace({ stage, mode, modeControl, agentPanel, workRequest, children }: {
  stage: CreationStage; mode: CreationMode; modeControl: ReactNode; agentPanel: ReactNode; workRequest: number; children: ReactNode;
}) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [editorTarget, setEditorTarget] = useState<HTMLDivElement | null>(null);
  const [editorStage, setEditorStage] = useState<CreationStage | null>(null);
  const [panelRequest, setPanelRequest] = useState(0);
  const [editorDismissRequest, setEditorDismissRequest] = useState(0);
  const revealPanel = useCallback(() => setPanelRequest(previous => previous + 1), []);
  const setEditor = useCallback((nextStage: CreationStage, active: boolean) => {
    if (active) {
      setEditorStage(nextStage);
      setPanelRequest(previous => previous + 1);
    } else {
      setEditorStage(previous => previous === nextStage ? null : previous);
      setEditorDismissRequest(previous => previous + 1);
    }
  }, []);
  const editing = editorStage === stage;
  const workspace = useMemo(() => ({ target, editorTarget, stage, mode, setEditor, revealPanel }), [target, editorTarget, stage, mode, setEditor, revealPanel]);
  return <CreationContext.Provider value={workspace}>
    <AgentEpisodeLayout enabled={stage !== 'assembly'} workRequest={workRequest + editorDismissRequest} panelRequest={panelRequest} panel={<section className={`episode-creation-panel${stage === 'storyboard' && mode === 'prompt' ? ' is-storyboard-prompt' : ''}`} aria-label="AI 创作区域">
      <div className="creation-mode-toolbar" hidden={editing}>{modeControl}</div>
      <aside className="prompt-creation-panel" aria-label="提示词 AI 创作" hidden={editing || mode !== 'prompt'}>
        <header className="creation-panel-heading" hidden={stage === 'storyboard'}><h2>AI 创作</h2><span>模型与生成设置</span></header>
        <div ref={setTarget} className="creation-panel-content" />
      </aside>
      <div hidden={editing || mode !== 'agent'}>{agentPanel}</div>
      <div ref={setEditorTarget} className="creation-editor-target" hidden={!editing}/>
    </section>}>{children}</AgentEpisodeLayout>
  </CreationContext.Provider>;
}

/** Keep each editor as the state owner while placing its controls in the shared rail. */
export function CreationSlot({ stage, children }: { stage: CreationStage; children: ReactNode }) {
  const workspace = useContext(CreationContext);
  if (!workspace) return <>{children}</>;
  return workspace.target ? createPortal(<div className="creation-slot" hidden={workspace.stage !== stage}>{children}</div>, workspace.target) : null;
}

/** Move the existing editing session without replacing the mounted conversation. */
export function EpisodeEditorSlot({ stage, children, active = true }: {
  stage: CreationStage; children: ReactNode; active?: boolean;
}) {
  const workspace = useContext(CreationContext);
  const setEditor = workspace?.setEditor;
  const visible = active && (!workspace || workspace.stage === stage);
  useLayoutEffect(() => {
    if (!setEditor || !visible) return;
    setEditor(stage, true);
    return () => setEditor(stage, false);
  }, [setEditor, stage, visible]);
  if (!workspace) return active ? <>{children}</> : null;
  return workspace.editorTarget ? createPortal(
    <div className="episode-editor-slot" hidden={!visible}>{children}</div>, workspace.editorTarget,
  ) : null;
}
