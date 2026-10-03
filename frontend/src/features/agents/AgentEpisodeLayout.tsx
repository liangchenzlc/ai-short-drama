import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';

export function AgentEpisodeLayout({ enabled, children, panel, workRequest = 0 }: { enabled: boolean; children: ReactNode; panel: ReactNode; workRequest?: number }) {
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; x: number; width: number } | null>(null);
  const [frameWidth, setFrameWidth] = useState(0);
  const [width, setWidth] = useState(400);
  const [view, setView] = useState<'work' | 'conversation'>('work');
  useEffect(() => { if (workRequest) setView('work'); }, [workRequest]);
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
  const max = Math.min(480, Math.max(360, frameWidth - 580));
  const panelWidth = Math.min(width, max);
  const changeWidth = (next: number) => setWidth(Math.round(Math.max(360, Math.min(max, next))));
  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>) {
    const next = event.key === 'Home' ? 'work' : event.key === 'End' ? 'conversation'
      : event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? (view === 'work' ? 'conversation' : 'work') : undefined;
    if (!next) return;
    event.preventDefault(); setView(next);
    frame.current?.querySelector<HTMLButtonElement>(`#agent-${next}-tab`)?.focus();
  }
  return <div ref={frame} className={`agent-episode-layout${enabled ? ' is-agent' : ''}${wide ? ' is-wide' : ' is-compact'}`}
    data-agent-view={view} style={{ '--agent-panel-width': `${panelWidth}px` } as CSSProperties}>
    {enabled && !wide && <div className="agent-workspace-tabs" role="tablist" aria-label="创作工作区">
      <button type="button" role="tab" id="agent-work-tab" tabIndex={view === 'work' ? 0 : -1} aria-selected={view === 'work'} aria-controls="agent-work-pane" onKeyDown={navigateTabs} onClick={() => setView('work')}>作品</button>
      <button type="button" role="tab" id="agent-conversation-tab" tabIndex={view === 'conversation' ? 0 : -1} aria-selected={view === 'conversation'} aria-controls="agent-conversation-pane" onKeyDown={navigateTabs} onClick={() => setView('conversation')}>AI 创作</button>
    </div>}
    <div id="agent-work-pane" className="agent-work-pane" hidden={enabled && !wide && view !== 'work'}
      role={enabled && !wide ? 'tabpanel' : undefined} aria-labelledby={enabled && !wide ? 'agent-work-tab' : undefined}>{children}</div>
    <div className="agent-panel-resize" role="separator" tabIndex={enabled && wide ? 0 : -1} hidden={!enabled || !wide}
      aria-label="调整对话区域宽度" aria-orientation="vertical" aria-controls="agent-conversation-pane"
      aria-valuemin={360} aria-valuemax={max} aria-valuenow={panelWidth} aria-valuetext={`${panelWidth} 像素`}
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
        const next = { ArrowLeft: panelWidth + 20, ArrowRight: panelWidth - 20, Home: 360, End: max }[event.key];
        if (next !== undefined) { event.preventDefault(); changeWidth(next); }
      }}><span aria-hidden="true" /></div>
    <div id="agent-conversation-pane" className="agent-conversation-pane" hidden={!enabled || !wide && view !== 'conversation'}
      role={enabled && !wide ? 'tabpanel' : undefined} aria-labelledby={enabled && !wide ? 'agent-conversation-tab' : undefined}>{panel}</div>
  </div>;
}
