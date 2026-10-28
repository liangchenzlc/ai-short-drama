import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Wand2 } from 'lucide-react';
import type { ScriptSelection } from './writing-requests';
import { ScriptHistory } from './editor-history';
import { captureScriptSelection } from './script-selection';

type Highlight = Pick<ScriptSelection, 'start' | 'end' | 'source'>;

export function ScriptSceneEditor({ script, readOnly, rewriteDisabled, loaded, frozenSelection, onChange, onRewrite }: {
  script: string; readOnly: boolean; rewriteDisabled: boolean; loaded: boolean;
  frozenSelection: ScriptSelection | null;
  onChange: (text: string) => void; onRewrite: (selection: ScriptSelection) => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null), scroll = useRef<HTMLDivElement>(null), display = useRef<HTMLDivElement>(null);
  const composing = useRef(false), dragging = useRef(false);
  const [composition, setComposition] = useState<string | null>(null);
  const [selection, setSelection] = useState<ScriptSelection | null>(null);
  const [floating, setFloating] = useState(false);
  const [hint, setHint] = useState('');
  const [result, setResult] = useState<Highlight | null>(null);
  const history = useRef(new ScriptHistory(script));
  const initialized = useRef(loaded);
  const lastFrozen = useRef<ScriptSelection | null>(null);
  const helpId = useId();
  const value = composition ?? script;

  useLayoutEffect(() => {
    const previous = lastFrozen.current;
    let replacement: Highlight | null = null;
    if (!initialized.current && loaded) {
      history.current = new ScriptHistory(script); initialized.current = true;
    } else if (loaded && !composing.current && history.current.text !== script) {
      if (previous && previous.source === history.current.text) {
        const end = script.length - (previous.source.length - previous.end);
        if (end >= previous.start && script.startsWith(previous.source.slice(0, previous.start)) && script.endsWith(previous.source.slice(previous.end))) {
          replacement = { source: script, start: previous.start, end }; setResult(replacement);
        }
      }
      history.current.record(script, replacement?.start ?? input.current?.selectionStart ?? 0, replacement?.end ?? input.current?.selectionEnd ?? 0);
      setSelection(null); setFloating(false);
    }
    if (frozenSelection) lastFrozen.current = frozenSelection;
    else if (previous && !readOnly) {
      lastFrozen.current = null;
      const restore = replacement ?? (previous.source === script ? previous : null);
      if (restore) {
        const frame = requestAnimationFrame(() => {
          input.current?.focus({ preventScroll: true });
          input.current?.setSelectionRange(restore.start, restore.end);
        });
        return () => cancelAnimationFrame(frame);
      }
    }
  }, [script, loaded, frozenSelection, readOnly]);
  useEffect(() => {
    if (!result) return;
    const timer = setTimeout(() => setResult(null), 1200);
    return () => clearTimeout(timer);
  }, [result]);
  useEffect(() => {
    const release = () => {
      if (!dragging.current) return;
      dragging.current = false;
      requestAnimationFrame(() => updateSelection());
    };
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
    return () => { window.removeEventListener('pointerup', release); window.removeEventListener('pointercancel', release); };
  });
  useEffect(() => {
    if (!selection || frozenSelection) return;
    const clear = (event: PointerEvent) => {
      if (event.target instanceof Element && !event.target.closest('.sd-selbar,.sd-input')) { setSelection(null); setFloating(false); }
    };
    const resize = () => { if (input.current?.getClientRects().length) updateSelection(); };
    document.addEventListener('pointerdown', clear);
    const observer = new ResizeObserver(resize);
    if (display.current) observer.observe(display.current);
    window.addEventListener('resize', resize);
    return () => { document.removeEventListener('pointerdown', clear); observer.disconnect(); window.removeEventListener('resize', resize); };
  }, [selection?.start, selection?.end, frozenSelection, readOnly, rewriteDisabled]);

  function updateSelection(open = false) {
    const node = input.current;
    if (!node || composing.current || dragging.current) return;
    history.current.remember(node.selectionStart, node.selectionEnd);
    if (readOnly || rewriteDisabled || !display.current || !scroll.current) { setSelection(null); setFloating(false); return; }
    const captured = captureScriptSelection(node, display.current, scroll.current);
    setSelection(captured); setFloating(!!captured);
    if (open) {
      if (captured) { setHint(''); setFloating(false); onRewrite(captured); }
      else setHint('先选中需要改写的文字');
    }
  }
  function openRewrite() {
    if (!selection || readOnly || rewriteDisabled) return;
    setFloating(false); setHint(''); onRewrite(selection);
  }
  function commit(node: HTMLTextAreaElement, typing = false) {
    if (readOnly) return;
    lastFrozen.current = null; setResult(null); setSelection(null); setFloating(false); setHint('');
    history.current.record(node.value, node.selectionStart, node.selectionEnd, typing);
    onChange(node.value);
  }
  function finishComposition(node: HTMLTextAreaElement) {
    if (!composing.current) return;
    composing.current = false; setComposition(null); commit(node);
  }
  function undo(direction: -1 | 1) {
    if (readOnly || composing.current) return;
    const entry = history.current.move(direction);
    if (!entry) return;
    lastFrozen.current = null; setResult(null); setSelection(null); setFloating(false); setHint('');
    onChange(entry.text);
    requestAnimationFrame(() => { input.current?.focus({ preventScroll: true }); input.current?.setSelectionRange(entry.start, entry.end); });
  }
  const highlight = frozenSelection?.source === value ? frozenSelection : result?.source === value ? result : null;
  const canRewrite = !!selection && !readOnly && !rewriteDisabled && !frozenSelection;
  const anchorTop = selection && scroll.current && selection.top - scroll.current.getBoundingClientRect().top >= 44
    ? selection.top - 42 : (selection?.bottom ?? 0) + 8;
  return <div className="sd-editor">
    <div className="sd-scroll" ref={scroll} onScroll={() => setFloating(false)}>
      <div className="sd-page"><div className={`sd-body${frozenSelection ? ' has-frozen-selection' : ''}`}>
        <div className="sd-highlight" ref={display} aria-hidden="true">{highlight ? <>{value.slice(0, highlight.start)}<mark className={result === highlight ? 'sd-result-highlight' : 'sd-range-highlight'}>{value.slice(highlight.start, highlight.end)}</mark>{value.slice(highlight.end)}</> : value}{!value || value.endsWith('\n') ? '\u200b' : null}</div>
        <textarea ref={input} className="sd-input" value={value} readOnly={readOnly || !loaded} spellCheck={false} aria-label="剧本正文" aria-describedby={helpId} placeholder="在这里写剧本，也可以让助手起草。"
          onCompositionStart={event => { composing.current = true; setComposition(event.currentTarget.value); setSelection(null); setFloating(false); }}
          onCompositionEnd={event => finishComposition(event.currentTarget)} onBlur={event => finishComposition(event.currentTarget)}
          onChange={event => { if (composing.current) setComposition(event.target.value); else commit(event.target, (event.nativeEvent as InputEvent).inputType === 'insertText' && !event.target.value.endsWith('\n')); }}
          onSelect={() => updateSelection()}
          onPointerDown={event => { dragging.current = event.pointerType === 'mouse'; setSelection(null); setFloating(false); setResult(null); }}
          onKeyDown={event => {
            if (event.nativeEvent.isComposing || composing.current) return;
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); updateSelection(true); }
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); undo(event.shiftKey ? 1 : -1); }
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); undo(1); }
            if (event.key === 'Escape') { setSelection(null); setFloating(false); setHint(''); }
          }}/>
      </div></div>
    </div>
    <span className="sd-editor-help sd-editor-hint" role="status">{hint}</span>
    <span id={helpId} className="sd-editor-help">修改后自动保存。选中文字，按 Ctrl 或 Command 加 K 使用 AI 改写；改写完成后可撤销。</span>
    {canRewrite && floating && createPortal(<button type="button" className="sd-selbar" style={{ top: Math.min(window.innerHeight - 48, Math.max(12, anchorTop)), left: Math.min(window.innerWidth - 116, Math.max(12, selection.left)) }} onMouseDown={event => event.preventDefault()} onClick={openRewrite}><Wand2 size={14}/>AI 改写</button>, document.body)}
  </div>;
}
