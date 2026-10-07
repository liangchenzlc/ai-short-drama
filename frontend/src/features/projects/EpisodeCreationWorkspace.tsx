import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AgentEpisodeLayout } from '../agents/AgentEpisodeLayout';
import type { AgentSubject } from '../agents/agent-scope';
import './episode-creation.css';

type CreationStage = 'source' | 'assets' | 'storyboard' | 'assembly';
const CreationContext = createContext<{
  target: HTMLDivElement | null;
  editorTarget: HTMLDivElement | null;
  stage: CreationStage;
  revealPanel: () => void;
  setEditor: (stage: CreationStage, active: boolean) => void;
  subject: AgentSubject | null;
  selectSubject: (subject: AgentSubject | null) => void;
} | null>(null);

export function useEpisodeCreation() {
  return useContext(CreationContext) !== null;
}

export function useEpisodeCreationControls() {
  const workspace = useContext(CreationContext);
  return { revealPanel: workspace?.revealPanel, subject: workspace?.subject ?? null, selectSubject: workspace?.selectSubject };
}

export function EpisodeCreationWorkspace({ stage, assistantOpen, assistantPanel, onCloseAssistant, workRequest, children, subject, onSubject }: {
  stage: CreationStage; assistantOpen: boolean; assistantPanel: ReactNode; onCloseAssistant: () => void; workRequest: number; children: ReactNode;
  subject: AgentSubject | null; onSubject: (subject: AgentSubject | null) => void;
}) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [editorTarget, setEditorTarget] = useState<HTMLDivElement | null>(null);
  const [editorStage, setEditorStage] = useState<CreationStage | null>(null);
  const [panelRequest, setPanelRequest] = useState(0);
  const [editorDismissRequest, setEditorDismissRequest] = useState(0);
  const closeAssistant = useRef(onCloseAssistant); closeAssistant.current = onCloseAssistant;
  const revealPanel = useCallback(() => { closeAssistant.current(); setPanelRequest(previous => previous + 1); }, []);
  const setEditor = useCallback((nextStage: CreationStage, active: boolean) => {
    if (active) {
      closeAssistant.current();
      setEditorStage(nextStage);
      setPanelRequest(previous => previous + 1);
    } else {
      setEditorStage(previous => previous === nextStage ? null : previous);
      setEditorDismissRequest(previous => previous + 1);
    }
  }, []);
  const editing = editorStage === stage;
  const workspace = useMemo(() => ({ target, editorTarget, stage, setEditor, revealPanel, subject, selectSubject: onSubject }), [target, editorTarget, stage, setEditor, revealPanel, subject, onSubject]);
  return <CreationContext.Provider value={workspace}>
    <AgentEpisodeLayout enabled={stage !== 'assembly' || assistantOpen} assistantOpen={assistantOpen} onCloseAssistant={onCloseAssistant} workRequest={workRequest + editorDismissRequest} panelRequest={panelRequest} panel={<section className={`episode-creation-panel${stage === 'storyboard' && !assistantOpen ? ' is-storyboard-settings' : ''}${assistantOpen ? ' is-assistant-open' : ''}`} aria-label={assistantOpen ? 'AI 创作助手区域' : '模型与生成设置'}>
      <aside className="generation-settings-panel" aria-label="模型与生成设置" hidden={editing || assistantOpen}>
        <header className="creation-panel-heading" hidden={stage === 'storyboard'}><h2>AI 创作</h2><span>模型与生成设置</span></header>
        <div ref={setTarget} className="creation-panel-content" />
      </aside>
      <div className="episode-assistant-target" hidden={!assistantOpen}>{assistantPanel}</div>
      <div ref={setEditorTarget} className="creation-editor-target" hidden={!editing || assistantOpen}/>
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
