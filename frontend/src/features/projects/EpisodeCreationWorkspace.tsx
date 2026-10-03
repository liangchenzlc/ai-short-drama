import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AgentEpisodeLayout } from '../agents/AgentEpisodeLayout';
import type { CreationMode } from '../agents/agent-navigation';
import './episode-creation.css';

type CreationStage = 'source' | 'assets' | 'storyboard' | 'assembly';
const CreationContext = createContext<{ target: HTMLDivElement | null; stage: CreationStage } | null>(null);

export function useEpisodeCreation() {
  return useContext(CreationContext) !== null;
}

export function EpisodeCreationWorkspace({ stage, mode, modeControl, agentPanel, workRequest, children }: {
  stage: CreationStage; mode: CreationMode; modeControl: ReactNode; agentPanel: ReactNode; workRequest: number; children: ReactNode;
}) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const workspace = useMemo(() => ({ target, stage }), [target, stage]);
  return <CreationContext.Provider value={workspace}>
    <AgentEpisodeLayout enabled={stage !== 'assembly'} workRequest={workRequest} panel={<section className="episode-creation-panel" aria-label="AI 创作区域">
      <div className="creation-mode-toolbar">{modeControl}</div>
      <aside className="prompt-creation-panel" aria-label="提示词 AI 创作" hidden={mode !== 'prompt'}>
        <header className="creation-panel-heading"><h2>AI 创作</h2><span>模型与生成设置</span></header>
        <div ref={setTarget} className="creation-panel-content" />
      </aside>
      <div hidden={mode !== 'agent'}>{agentPanel}</div>
    </section>}>{children}</AgentEpisodeLayout>
  </CreationContext.Provider>;
}

/** Keep each editor as the state owner while placing its controls in the shared rail. */
export function CreationSlot({ stage, children }: { stage: CreationStage; children: ReactNode }) {
  const workspace = useContext(CreationContext);
  if (!workspace) return <>{children}</>;
  return workspace.target ? createPortal(<div className="creation-slot" hidden={workspace.stage !== stage}>{children}</div>, workspace.target) : null;
}
