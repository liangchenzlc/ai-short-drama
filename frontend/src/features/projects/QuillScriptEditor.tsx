import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Skeleton, Tooltip } from 'antd';
import { Bold, Italic, Quote, Undo2, Redo2 } from 'lucide-react';
import type Quill from 'quill';
import type { Delta, EmitterSource } from 'quill';
import type { EditorDocument } from '../../api/modules/episode-writing';
import { documentText, normalizeDocument, sameDocument, scriptScenes, scriptWordCount, textDocument } from './script-document';
import 'quill/dist/quill.snow.css';
import './novel-editor.css';

const quillOps = (document: EditorDocument) => document.ops.map(op => ({ insert: op.insert, ...(op.attributes ? { attributes: { ...op.attributes } } : {}) }));

export function QuillScriptEditor({ content, document, readOnly, onChange, label = '本集剧本正文', toolbarActions }: {
  content: string;
  document?: EditorDocument | null;
  readOnly: boolean;
  onChange?: (content: string, document: EditorDocument) => void;
  label?: string;
  toolbarActions?: ReactNode;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<Quill | null>(null);
  const latest = useRef({ content, document, readOnly, onChange, label });
  latest.current = { content, document, readOnly, onChange, label };
  const composing = useRef(false);
  const emitted = useRef('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [tools, setTools] = useState({ bold: false, italic: false, blockquote: false, undo: false, redo: false });
  const scenes = useMemo(() => scriptScenes(content), [content]);

  useEffect(() => {
    let active = true;
    let cleanup = () => {};
    setLoading(true); setError('');
    import('quill').then(({ default: QuillClass }) => {
      if (!active || !host.current) return;
      const container = window.document.createElement('div');
      host.current.replaceChildren(container);
      const quill = new QuillClass(container, {
        theme: 'snow', readOnly: latest.current.readOnly,
        formats: ['header', 'bold', 'italic', 'blockquote'],
        placeholder: '粘贴或编写本集剧本…',
        modules: { toolbar: false, history: { delay: 1000, maxStack: 100, userOnly: true } },
      });
      quill.setContents(quillOps(latest.current.document ?? textDocument(latest.current.content)), 'silent');
      quill.history.clear();
      quill.root.setAttribute('role', 'textbox');
      quill.root.setAttribute('aria-label', latest.current.label);
      quill.root.setAttribute('aria-multiline', 'true');
      quill.root.setAttribute('aria-readonly', String(latest.current.readOnly));
      quill.root.spellcheck = false;
      // The persistence contract supports heading levels 1–3 only.
      quill.clipboard.addMatcher('h4, h5, h6', (_node, delta) => {
        for (const op of delta.ops) if (op.attributes?.header) op.attributes.header = 3;
        return delta;
      });
      editor.current = quill;
      emitted.current = JSON.stringify(normalizeDocument((latest.current.document ?? textDocument(latest.current.content))));
      let compositionTimer: ReturnType<typeof setTimeout> | undefined;
      const updateTools = () => {
        if (!active) return;
        const format = quill.getFormat();
        setTools({ bold: !!format.bold, italic: !!format.italic, blockquote: !!format.blockquote, undo: !!quill.history.stack.undo.length, redo: !!quill.history.stack.redo.length });
      };
      const emitChange = () => {
        if (!active || composing.current || latest.current.readOnly) return;
        const delta = normalizeDocument(quill.getContents() as EditorDocument);
        const key = JSON.stringify(delta);
        if (key !== emitted.current) {
          emitted.current = key;
          latest.current.onChange?.(documentText(delta), delta);
        }
        updateTools();
      };
      const change = (_delta: Delta, _old: Delta, source: EmitterSource) => {
        if (source === 'user') emitChange();
      };
      const startComposition = () => { composing.current = true; };
      const endComposition = () => {
        composing.current = false;
        clearTimeout(compositionTimer);
        compositionTimer = setTimeout(() => { quill.update('user'); emitChange(); }, 0);
      };
      quill.on('text-change', change);
      quill.on('selection-change', updateTools);
      quill.root.addEventListener('compositionstart', startComposition);
      quill.root.addEventListener('compositionend', endComposition);
      updateTools(); setLoading(false);
      cleanup = () => {
        clearTimeout(compositionTimer);
        quill.off('text-change', change);
        quill.off('selection-change', updateTools);
        quill.root.removeEventListener('compositionstart', startComposition);
        quill.root.removeEventListener('compositionend', endComposition);
        quill.disable();
        quill.scroll.detach();
        quill.emitter.removeAllListeners();
        editor.current = null;
        container.remove();
      };
    }).catch(() => { if (active) { setError('剧本编辑器加载失败，正文已保留，请重试。'); setLoading(false); } });
    return () => { active = false; composing.current = false; cleanup(); };
  }, [revision]);

  useLayoutEffect(() => {
    const quill = editor.current;
    if (!quill) return;
    quill.enable(!readOnly);
    quill.root.setAttribute('aria-readonly', String(readOnly));
    quill.root.setAttribute('aria-label', label);
    if (composing.current) return;
    const current = quill.getContents() as EditorDocument;
    if (documentText(current) !== content || !sameDocument(current, document, content)) {
      quill.setContents(quillOps(document ?? textDocument(content)), 'silent');
      emitted.current = JSON.stringify(normalizeDocument(document ?? textDocument(content)));
      quill.history.clear();
      setTools({ bold: false, italic: false, blockquote: false, undo: false, redo: false });
    }
  }, [content, document, readOnly, label]);

  function format(name: 'bold' | 'italic' | 'blockquote') {
    const quill = editor.current;
    if (!quill || readOnly || composing.current) return;
    quill.format(name, !quill.getFormat()[name], 'user');
  }
  function locate(index: number) {
    const quill = editor.current;
    if (!quill || composing.current) return;
    quill.setSelection(index, 0, 'silent');
    quill.focus({ preventScroll: true });
    quill.scrollSelectionIntoView();
  }

  return <div className="novel-quill-workspace">
    <div className="novel-script-toolbar">
      {!readOnly && <div className="novel-format-tools" role="toolbar" aria-label="剧本格式">
        <Tooltip title="加粗"><Button type="text" aria-label="加粗" aria-pressed={tools.bold} disabled={loading || !!error} icon={<Bold size={16}/>} onMouseDown={event => event.preventDefault()} onClick={() => format('bold')}/></Tooltip>
        <Tooltip title="斜体"><Button type="text" aria-label="斜体" aria-pressed={tools.italic} disabled={loading || !!error} icon={<Italic size={16}/>} onMouseDown={event => event.preventDefault()} onClick={() => format('italic')}/></Tooltip>
        <Tooltip title="引用"><Button type="text" aria-label="引用" aria-pressed={tools.blockquote} disabled={loading || !!error} icon={<Quote size={16}/>} onMouseDown={event => event.preventDefault()} onClick={() => format('blockquote')}/></Tooltip>
        <Tooltip title="撤销编辑"><Button type="text" aria-label="撤销编辑" disabled={loading || !tools.undo} icon={<Undo2 size={16}/>} onClick={() => editor.current?.history.undo()}/></Tooltip>
        <Tooltip title="重做编辑"><Button type="text" aria-label="重做编辑" disabled={loading || !tools.redo} icon={<Redo2 size={16}/>} onClick={() => editor.current?.history.redo()}/></Tooltip>
      </div>}
      <span>{scriptWordCount(content).toLocaleString()} 字</span><span>{scenes.length} 场</span>
      {toolbarActions}
      {scenes.length > 0 && <div className="novel-scene-shortcuts" aria-label="场次定位">{scenes.map((scene, index) => <Tooltip key={scene.index} title={scene.label}><Button size="small" type="text" aria-label={`定位到${scene.label}`} disabled={loading || !!error} onClick={() => locate(scene.index)}>场 {index + 1}</Button></Tooltip>)}</div>}
    </div>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setRevision(value => value + 1)}>重试</Button>}/>}
    {loading && <Skeleton className="novel-editor-loading" active paragraph={{ rows: 6 }}/>}
    <div ref={host} className="novel-quill-host" hidden={loading || !!error}/>
  </div>;
}
