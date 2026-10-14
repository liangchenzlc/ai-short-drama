import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from 'antd';
import { X } from 'lucide-react';
import { useEpisodeCreationControls } from './EpisodeCreationWorkspace';

export function ShotGenerationSettings({ open, active, onClose, children, id }: {
  open: boolean; active: boolean; onClose: () => void; children: ReactNode; id?: string;
}) {
  const [compact, setCompact] = useState(() => window.innerWidth <= 700);
  const pane = useRef<HTMLDivElement>(null);
  const close = useRef(onClose); close.current = onClose;
  const { assistantOpen } = useEpisodeCreationControls();
  const drawerOpen = compact && open && active && !assistantOpen;
  useLayoutEffect(() => {
    const query = window.matchMedia('(max-width: 700px)');
    const update = () => { setCompact(query.matches); if (query.matches) close.current(); };
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useLayoutEffect(() => {
    const panel = pane.current;
    if (!drawerOpen || !panel) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const controls = () => [...panel.querySelectorAll<HTMLElement>('button:not([disabled]),textarea:not([disabled]),input:not([disabled]),[tabindex="0"]')].filter(node => node.getClientRects().length > 0);
    const handle = (event: globalThis.KeyboardEvent) => {
      if (document.querySelector('[role="dialog"]:not(.standard-object-settings),.ant-select-dropdown:not(.ant-select-dropdown-hidden)')) return;
      if (event.key === 'Escape') { event.preventDefault(); close.current(); }
      if (event.key !== 'Tab') return;
      const first = controls()[0]; const last = controls().at(-1);
      if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    const focus = requestAnimationFrame(() => controls()[0]?.focus());
    document.addEventListener('keydown', handle);
    return () => { cancelAnimationFrame(focus); document.removeEventListener('keydown', handle); if (previous?.isConnected) previous.focus(); };
  }, [drawerOpen]);
  return <>
    {drawerOpen && <button type="button" className="shot-settings-backdrop" tabIndex={-1} aria-label="关闭分镜生成设置" onClick={onClose}/>}
    <div id={id} ref={pane} className="standard-object-settings" hidden={!open} role={drawerOpen ? 'dialog' : undefined} aria-modal={drawerOpen ? true : undefined} aria-label={drawerOpen ? '分镜生成设置' : undefined}>
      <header className="shot-settings-drawer-heading"><h3>生成设置</h3><Button type="text" aria-label="关闭分镜生成设置" title="关闭分镜生成设置" icon={<X size={16}/>} onClick={onClose}/></header>
      <div className="shot-settings-scroll">{children}</div>
    </div>
  </>;
}
