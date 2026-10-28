import { accountStorage } from '@host/features/identity/storage/account-storage';
import type { AgentSubject } from '@host/types/assistant/references/agent-scope';
import type { CreationStage } from '@host/types/creation/workspace/layout/CreationWorkspace';
import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import '../../../assistant/panel/standard-assistant.css';
import './index.css';
import './overlays.css';
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

export function useCreationWorkspace() {
  return useContext(CreationContext) !== null;
}

export function useCreationControls() {
  const workspace = useContext(CreationContext);
  return {
    revealPanel: workspace?.revealPanel,
    subject: workspace?.subject ?? null,
    selectSubject: workspace?.selectSubject,
    panelOpen: workspace?.panelOpen ?? true,
    setPanelOpen: workspace?.setPanelOpen,
    assistantOpen: workspace?.assistantOpen ?? false,
  };
}

export function CreationWorkspace({
  accountId,
  stage,
  assistantOpen,
  assistantPanel,
  workRequest,
  children,
  subject,
  onSubject,
}: {
  accountId?: string;
  stage: CreationStage;
  assistantOpen: boolean;
  assistantPanel: ReactNode;
  workRequest: number;
  children: ReactNode;
  subject: AgentSubject | null;
  onSubject: (subject: AgentSubject | null) => void;
}) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [editorTarget, setEditorTarget] = useState<HTMLDivElement | null>(null);
  const [editorStage, setEditorStage] = useState<CreationStage | null>(null);
  const [panelRequest, setPanelRequest] = useState(0);
  const [panelOpen, setPanelOpen] = useState(true);
  const [compactPreview, setCompactPreview] = useState(false);
  const frame = useRef<HTMLDivElement>(null);
  const [assistantWidth, setAssistantWidth] = useState(380);
  const [assistantMaxWidth, setAssistantMaxWidth] = useState(560);
  const resize = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  const visibleWidth = Math.min(assistantWidth, assistantMaxWidth);
  useLayoutEffect(() => {
    resize.current = null;
    let stored = 380;
    if (accountId) {
      try {
        const raw = accountStorage(accountId).getItem('creation:assistant-width');
        if (raw) stored = Number(raw);
      } catch {
        /* Width remains usable without browser storage. */
      }
    }
    setAssistantWidth(Number.isFinite(stored) ? Math.max(320, Math.min(560, Math.round(stored))) : 380);
  }, [accountId]);
  useLayoutEffect(() => {
    const element = frame.current;
    if (!element) return;
    const update = () => {
      const gap = Number.parseFloat(getComputedStyle(element).columnGap) || 0;
      setAssistantMaxWidth(Math.max(320, Math.min(560, Math.floor(element.clientWidth - 640 - gap))));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  function changeAssistantWidth(width: number) {
    const next = Math.max(320, Math.min(assistantMaxWidth, Math.round(width)));
    setAssistantWidth(next);
    if (accountId) {
      try {
        accountStorage(accountId).setItem('creation:assistant-width', String(next));
      } catch {
        /* Keep the in-session preference. */
      }
    }
  }
  const mainFrame = useRef<HTMLElement>(null);
  const revealPanel = useCallback(() => {
    setPanelOpen(true);
    setPanelRequest((previous) => previous + 1);
  }, []);
  const setEditor = useCallback((nextStage: CreationStage, active: boolean) => {
    if (active) {
      setEditorStage(nextStage);
      setPanelRequest((previous) => previous + 1);
    } else {
      setEditorStage((previous) => (previous === nextStage ? null : previous));
    }
  }, []);
  const editing = editorStage === stage;
  useLayoutEffect(() => {
    const panel = mainFrame.current;
    if (!panel) return;
    const update = () => {
      const base = Number.parseFloat(getComputedStyle(panel).getPropertyValue('--creation-settings-base'));
      setCompactPreview(panel.clientWidth < (base * 10) / 9 + 402);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(panel);
    window.addEventListener('resize', update);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [stage]);
  const workspace = useMemo(
    () => ({
      target,
      editorTarget,
      stage,
      setEditor,
      revealPanel,
      subject,
      selectSubject: onSubject,
      panelOpen,
      setPanelOpen,
      assistantOpen,
    }),
    [target, editorTarget, stage, setEditor, revealPanel, subject, onSubject, panelOpen, setPanelOpen, assistantOpen],
  );
  const workStage = (
    <div key="stage" className="creation-work-stage">
      {children}
    </div>
  );
  const mainPanel = (
    <section
      ref={mainFrame}
      key="panel"
      className={`creation-main-panel${compactPreview ? ' is-compact-preview' : ''}`}
      aria-label="作品与生成设置"
      hidden={stage === 'assembly'}
    >
      <div ref={setTarget} className="creation-panel-content" hidden={editing} />
      <div ref={setEditorTarget} className="creation-editor-target" hidden={!editing} />
    </section>
  );
  return (
    <CreationContext.Provider value={workspace}>
      <div
        ref={frame}
        style={{ '--creation-assistant-width': `${visibleWidth}px` } as CSSProperties}
        className={`standard-creation-layout is-${stage}${assistantOpen ? ' is-assistant-open' : ''}${panelOpen ? ' is-panel-open' : ' is-panel-closed'}`}
        data-panel-request={panelRequest + workRequest}
      >
        <div className="standard-creation-work">{stage === 'source' ? workStage : [workStage, mainPanel]}</div>
        {assistantOpen && (
          <button
            type="button"
            className="creation-assistant-resize"
            role="separator"
            aria-label="调整助手宽度"
            aria-controls="agent-conversation-pane"
            aria-orientation="vertical"
            aria-valuemin={320}
            aria-valuemax={assistantMaxWidth}
            aria-valuenow={visibleWidth}
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              event.preventDefault();
              event.currentTarget.focus();
              resize.current = {
                pointerId: event.pointerId,
                x: event.clientX,
                width: visibleWidth,
              };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              const start = resize.current;
              if (start?.pointerId === event.pointerId) changeAssistantWidth(start.width + start.x - event.clientX);
            }}
            onPointerUp={() => {
              resize.current = null;
            }}
            onPointerCancel={() => {
              resize.current = null;
            }}
            onLostPointerCapture={() => {
              resize.current = null;
            }}
            onKeyDown={(event) => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              changeAssistantWidth(
                event.key === 'Home'
                  ? 320
                  : event.key === 'End'
                    ? assistantMaxWidth
                    : visibleWidth + (event.key === 'ArrowLeft' ? 16 : -16),
              );
            }}
          />
        )}
        <aside
          id="agent-conversation-pane"
          className="creation-assistant-sidebar"
          hidden={!assistantOpen}
          aria-label="AI 创作助手区域"
        >
          {assistantPanel}
        </aside>
      </div>
    </CreationContext.Provider>
  );
}

/** Keep each editor as the state owner while placing its controls in the shared rail. */
export function CreationSlot({ stage, children }: { stage: CreationStage; children: ReactNode }) {
  const workspace = useContext(CreationContext);
  if (!workspace) return <>{children}</>;
  return workspace.target
    ? createPortal(
        <div className="creation-slot" hidden={workspace.stage !== stage}>
          {children}
        </div>,
        workspace.target,
      )
    : null;
}

/** Move the existing editing session without replacing the mounted conversation. */
export function CreationEditorSlot({
  stage,
  children,
  active = true,
}: {
  stage: CreationStage;
  children: ReactNode;
  active?: boolean;
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
  return workspace.editorTarget
    ? createPortal(
        <div className="creation-editor-slot" hidden={!visible}>
          {children}
        </div>,
        workspace.editorTarget,
      )
    : null;
}
