import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { Icon } from '../../components/ui/Icon';

export function AgentEpisodeLayout({ enabled, assistantOpen = false, onCloseAssistant, children, panel, workRequest = 0, panelRequest = 0 }: { enabled: boolean; assistantOpen?: boolean; onCloseAssistant?: () => void; children: ReactNode; panel: ReactNode; workRequest?: number; panelRequest?: number }) {
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; x: number; width: number } | null>(null);
  const [frameWidth, setFrameWidth] = useState(0);
  const [width, setWidth] = useState(380);
  const [collapsed, setCollapsed] = useState(false);
  const [view, setView] = useState<'work' | 'conversation'>('work');
  useEffect(() => { if (assistantOpen) { setCollapsed(false); setView('conversation'); } }, [assistantOpen]);
  useEffect(() => { if (workRequest) setView('work'); }, [workRequest]);
  useEffect(() => {
    if (!panelRequest) return;
    setCollapsed(false);
    setView('conversation');
  }, [panelRequest]);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => setFrameWidth(Math.round(element.getBoundingClientRect().width));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const wide = frameWidth >= 980;
  useEffect(() => {
    if (!assistantOpen || wide) return;
    const pane = frame.current?.querySelector<HTMLElement>('#agent-conversation-pane');
    if (!pane) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const controls = () => [...pane.querySelectorAll<HTMLElement>('button:not([disabled]),textarea:not([disabled]),input:not([disabled]),[tabindex="0"]')].filter(node => node.getClientRects().length > 0);
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onCloseAssistant?.(); }
      if (event.key !== 'Tab') return;
      const entries = controls(); const first = entries[0]; const last = entries.at(-1);
      if (!first || !last) { event.preventDefault(); pane.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || !pane.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !pane.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    const focus = requestAnimationFrame(() => (controls()[0] ?? pane).focus());
    pane.addEventListener('keydown', onKeyDown);
    return () => { cancelAnimationFrame(focus); pane.removeEventListener('keydown', onKeyDown); previous?.focus(); };
  }, [assistantOpen, wide, onCloseAssistant]);
  const railCollapsed = enabled && wide && collapsed;
  const min = assistantOpen ? 320 : 360;
  const max = Math.min(assistantOpen ? 560 : 480, Math.max(min, frameWidth - 580));
  const panelWidth = Math.min(width, max);
  const changeWidth = (next: number) => setWidth(Math.round(Math.max(min, Math.min(max, next))));
  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>) {
    const next = event.key === 'Home' ? 'work' : event.key === 'End' ? 'conversation'
      : event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? (view === 'work' ? 'conversation' : 'work') : undefined;
    if (!next) return;
    event.preventDefault(); setView(next);
    frame.current?.querySelector<HTMLButtonElement>(`#agent-${next}-tab`)?.focus();
  }
  return <div ref={frame} className={`agent-episode-layout${enabled ? ' is-agent' : ''}${wide ? ' is-wide' : ' is-compact'}${railCollapsed ? ' is-ai-collapsed' : ''}${assistantOpen ? ' is-assistant-open' : ''}`}
    data-agent-view={view} style={{ '--agent-panel-width': `${panelWidth}px` } as CSSProperties}>
    {enabled && !wide && !assistantOpen && <div className="agent-workspace-tabs" role="tablist" aria-label="创作工作区">
      <button type="button" role="tab" id="agent-work-tab" tabIndex={view === 'work' ? 0 : -1} aria-selected={view === 'work'} aria-controls="agent-work-pane" onKeyDown={navigateTabs} onClick={() => setView('work')}>作品</button>
      <button type="button" role="tab" id="agent-conversation-tab" tabIndex={view === 'conversation' ? 0 : -1} aria-selected={view === 'conversation'} aria-controls="agent-conversation-pane" onKeyDown={navigateTabs} onClick={() => setView('conversation')}>AI 创作</button>
    </div>}
    <div id="agent-work-pane" className="agent-work-pane" hidden={enabled && !wide && !assistantOpen && view !== 'work'} inert={assistantOpen && !wide ? true : undefined}
      role={enabled && !wide && !assistantOpen ? 'tabpanel' : undefined} aria-labelledby={enabled && !wide && !assistantOpen ? 'agent-work-tab' : undefined}>{children}</div>
    <div className="agent-panel-resize" role="separator" tabIndex={enabled && wide && !collapsed ? 0 : -1} hidden={!enabled || !wide || collapsed}
      aria-label="调整对话区域宽度" aria-orientation="vertical" aria-controls="agent-conversation-pane"
      aria-valuemin={min} aria-valuemax={max} aria-valuenow={panelWidth} aria-valuetext={`${panelWidth} 像素`}
      title="拖动调整宽度；左右方向键微调，Home / End 调至最窄 / 最宽"
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault(); event.currentTarget.focus();
        drag.current = { pointer: event.pointerId, x: event.clientX, width: panelWidth };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={event => {
        const start = drag.current;
        if (start?.pointer === event.pointerId) changeWidth(start.width + start.x - event.clientX);
      }}
      onPointerUp={event => {
        if (drag.current?.pointer !== event.pointerId) return;
        drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
      onKeyDown={event => {
        const next = { ArrowLeft: panelWidth + 20, ArrowRight: panelWidth - 20, Home: min, End: max }[event.key];
        if (next !== undefined) { event.preventDefault(); changeWidth(next); }
      }}><span aria-hidden="true" /></div>
    {assistantOpen && !wide && <button className="episode-assistant-backdrop" type="button" aria-label="关闭 AI 创作助手" tabIndex={-1} onClick={onCloseAssistant}/>}
    <div className="agent-creation-rail" hidden={!enabled || !wide && !assistantOpen && view !== 'conversation'}>
      <button type="button" className="agent-creation-collapse" hidden={!enabled || !wide}
        aria-label={railCollapsed ? '展开 AI 创作区域' : '收起 AI 创作区域'}
        title={railCollapsed ? '展开 AI 创作区域' : '收起 AI 创作区域'}
        aria-expanded={!railCollapsed} aria-controls="agent-conversation-pane"
        onClick={() => setCollapsed(previous => !previous)}>
        <Icon name={railCollapsed ? 'back' : 'arrow'} size={18} />
      </button>
      <div id="agent-conversation-pane" className="agent-conversation-pane" hidden={!enabled || railCollapsed || !wide && !assistantOpen && view !== 'conversation'} tabIndex={assistantOpen && !wide ? -1 : undefined}
        role={assistantOpen && !wide ? 'dialog' : enabled && !wide ? 'tabpanel' : undefined} aria-modal={assistantOpen && !wide ? true : undefined} aria-label={assistantOpen && !wide ? 'AI 创作助手' : undefined} aria-labelledby={enabled && !wide && !assistantOpen ? 'agent-conversation-tab' : undefined}>{panel}</div>
    </div>
  </div>;
}
