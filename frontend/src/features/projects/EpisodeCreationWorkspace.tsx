import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { AgentSubject } from '../agents/agent-scope';
import './episode-creation.css';
import './creation-overlays.css';
import '../ai-assistant/standard-assistant.css';

type CreationStage = 'source' | 'assets' | 'storyboard' | 'assembly';
const CreationContext = createContext<{
  target: HTMLDivElement | null;
  editorTarget: HTMLDivElement | null;
  stage: CreationStage;
  revealPanel: () => void;
  setEditor: (stage: CreationStage, active: boolean) => void;
  subject: AgentSubject | null;
  selectSubject: (subject: AgentSubject | null) => void;
  panelOpen: boolean;
  setPanelOpen: (open: boolean) => void;
  assistantOpen: boolean;
} | null>(null);

export function useEpisodeCreation() {
  return useContext(CreationContext) !== null;
}

export function useEpisodeCreationControls() {
  const workspace = useContext(CreationContext);
  return { revealPanel: workspace?.revealPanel, subject: workspace?.subject ?? null, selectSubject: workspace?.selectSubject, panelOpen: workspace?.panelOpen ?? true, setPanelOpen: workspace?.setPanelOpen, assistantOpen: workspace?.assistantOpen ?? false };
}

export function EpisodeCreationWorkspace({ stage, assistantOpen, assistantPanel, onCloseAssistant, workRequest, children, subject, onSubject }: {
  stage: CreationStage; assistantOpen: boolean; assistantPanel: ReactNode; onCloseAssistant: () => void; workRequest: number; children: ReactNode;
  subject: AgentSubject | null; onSubject: (subject: AgentSubject | null) => void;
}) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [editorTarget, setEditorTarget] = useState<HTMLDivElement | null>(null);
  const [editorStage, setEditorStage] = useState<CreationStage | null>(null);
  const [panelRequest, setPanelRequest] = useState(0);
  const [compact, setCompact] = useState(() => window.innerWidth < 1000);
  const [panelOpen, changePanelOpen] = useState(() => window.innerWidth > 700);
  const [compactPreview, setCompactPreview] = useState(false);
  const frame = useRef<HTMLDivElement>(null);
  const mainFrame = useRef<HTMLElement>(null);
  const panelTrigger = useRef<HTMLElement | null>(null);
  const closeAssistant = useRef(onCloseAssistant); closeAssistant.current = onCloseAssistant;
  const setPanelOpen = useCallback((open: boolean) => { if (open) { panelTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; if (window.innerWidth < 1000) closeAssistant.current(); } changePanelOpen(open); }, []);
  const revealPanel = useCallback(() => { if (window.innerWidth < 1000) closeAssistant.current(); changePanelOpen(true); setPanelRequest(previous => previous + 1); }, []);
  const setEditor = useCallback((nextStage: CreationStage, active: boolean) => {
    if (active) {
      setEditorStage(nextStage);
      setPanelRequest(previous => previous + 1);
    } else {
      setEditorStage(previous => previous === nextStage ? null : previous);
    }
  }, []);
  const editing = editorStage === stage;
  useLayoutEffect(() => {
    const panel = mainFrame.current;
    if (!panel) return;
    const update = () => {
      const base = Number.parseFloat(getComputedStyle(panel).getPropertyValue('--creation-settings-base'));
      setCompactPreview(panel.clientWidth < base * 10 / 9 + 402);
    };
    update();
    const observer = new ResizeObserver(update); observer.observe(panel);
    window.addEventListener('resize', update);
    return () => { observer.disconnect(); window.removeEventListener('resize', update); };
  }, [stage]);
  useLayoutEffect(() => {
    if (stage !== 'source' || !panelOpen || assistantOpen || window.innerWidth > 700) return;
    const pane = frame.current?.querySelector<HTMLElement>('.creation-main-panel');
    if (!pane) return;
    const controls = () => [...pane.querySelectorAll<HTMLElement>('button:not([disabled]),textarea:not([disabled]),input:not([disabled]),[tabindex="0"]')].filter(node => node.getClientRects().length > 0);
    const handle = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector('dialog[open]')) return;
      if (document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')) return;
      if (event.key === 'Escape') { event.preventDefault(); changePanelOpen(false); panelTrigger.current?.focus(); }
      if (event.key !== 'Tab') return;
      const first = controls()[0]; const last = controls().at(-1);
      if (event.shiftKey && (document.activeElement === first || !pane.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !pane.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    const focus = requestAnimationFrame(() => controls()[0]?.focus());
    document.addEventListener('keydown', handle);
    return () => { cancelAnimationFrame(focus); document.removeEventListener('keydown', handle); };
  }, [stage, panelOpen, assistantOpen]);
  useLayoutEffect(() => {
    const query = window.matchMedia('(max-width: 999px)');
    const update = () => setCompact(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useLayoutEffect(() => {
    if (!assistantOpen || !compact) return;
    const pane = frame.current?.querySelector<HTMLElement>('#agent-conversation-pane');
    if (!pane) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const controls = () => [...pane.querySelectorAll<HTMLElement>('button:not([disabled]),textarea:not([disabled]),input:not([disabled]),[tabindex="0"]')].filter(node => node.getClientRects().length > 0);
    const handle = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector('dialog[open], .ant-select-dropdown:not(.ant-select-dropdown-hidden), .ant-dropdown:not(.ant-dropdown-hidden)')) return;
      if (event.key === 'Escape') { event.preventDefault(); closeAssistant.current(); }
      if (event.key !== 'Tab') return;
      const first = controls()[0]; const last = controls().at(-1);
      if (!first || !last) { event.preventDefault(); pane.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || !pane.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !pane.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    const focus = requestAnimationFrame(() => (controls()[0] ?? pane).focus());
    pane.addEventListener('keydown', handle);
    return () => { cancelAnimationFrame(focus); pane.removeEventListener('keydown', handle); previous?.focus(); };
  }, [assistantOpen, compact]);
  const workspace = useMemo(() => ({ target, editorTarget, stage, setEditor, revealPanel, subject, selectSubject: onSubject, panelOpen, setPanelOpen, assistantOpen }), [target, editorTarget, stage, setEditor, revealPanel, subject, onSubject, panelOpen, setPanelOpen, assistantOpen]);
  const workStage = <div key="stage" className="episode-work-stage">{children}</div>;
  const mainPanel = <section ref={mainFrame} key="panel" className={`creation-main-panel${compactPreview ? ' is-compact-preview' : ''}`} aria-label={stage === 'source' ? '剧本改编设置' : '作品与生成设置'} hidden={stage === 'assembly' || stage === 'source' && !panelOpen}>
    <div ref={setTarget} className="creation-panel-content" hidden={editing}/>
    <div ref={setEditorTarget} className="creation-editor-target" hidden={!editing}/>
  </section>;
  return <CreationContext.Provider value={workspace}>
    <div ref={frame} className={`standard-creation-layout is-${stage}${assistantOpen ? ' is-assistant-open' : ''}${panelOpen ? ' is-panel-open' : ' is-panel-closed'}`} data-panel-request={panelRequest + workRequest}>
      <div className="standard-creation-work" inert={compact && assistantOpen ? true : undefined}>
        {stage === 'source' ? [mainPanel, workStage] : [workStage, mainPanel]}
      </div>
      {assistantOpen && compact && <button type="button" className="episode-assistant-backdrop" aria-label="关闭 AI 创作助手" tabIndex={-1} onClick={onCloseAssistant}/>}
      <aside id="agent-conversation-pane" className="episode-assistant-sidebar" hidden={!assistantOpen} aria-label="AI 创作助手区域" role={compact && assistantOpen ? 'dialog' : undefined} aria-modal={compact && assistantOpen ? true : undefined} tabIndex={compact && assistantOpen ? -1 : undefined}>{assistantPanel}</aside>
    </div>
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
