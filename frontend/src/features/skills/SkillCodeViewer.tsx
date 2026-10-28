import { useMemo } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { yaml } from '@codemirror/lang-yaml';

const theme = EditorView.theme({
  '&': { height: '100%', backgroundColor: 'var(--surface)', color: 'var(--ink)', fontSize: '13px' },
  '&.cm-focused': { outline: '2px solid var(--accent-border)', outlineOffset: '-2px' },
  '.cm-scroller': { fontFamily: 'Consolas, monospace', lineHeight: '1.8', overflow: 'auto' },
  '.cm-content': { padding: '14px 0', minHeight: '100%' },
  '.cm-gutters': { backgroundColor: 'var(--surface)', color: 'var(--sub)', borderRight: '1px solid var(--hair)' },
  '.cm-line': { paddingLeft: '14px', paddingRight: '20px' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': { backgroundColor: 'var(--surface-hover) !important' },
}, { dark: true });

export default function SkillCodeViewer({ path, content, scroll, onScroll }: {
  path: string; content: string; scroll: { top: number; left: number };
  onScroll: (value: { top: number; left: number }) => void;
}) {
  const extensions = useMemo(() => [theme,
    EditorView.contentAttributes.of({ tabindex: '0', role: 'textbox', 'aria-readonly': 'true',
      'aria-multiline': 'true', 'aria-label': `只读文件 ${path}` }),
    ...(/\.md$/i.test(path) ? [markdown()] : /\.json$/i.test(path) ? [json()] : /\.ya?ml$/i.test(path) ? [yaml()] : []),
  ], [path]);
  return <CodeMirror value={content} height="100%" readOnly editable={false} indentWithTab={false}
    extensions={extensions} theme="none" basicSetup={{ lineNumbers: true, foldGutter: false,
      highlightActiveLine: false, highlightActiveLineGutter: false, autocompletion: false,
      history: false, closeBrackets: false, bracketMatching: false, highlightSelectionMatches: false }}
    onCreateEditor={view => {
      view.requestMeasure({ read: () => scroll, write: position => { view.scrollDOM.scrollTop = position.top; view.scrollDOM.scrollLeft = position.left; } });
      view.scrollDOM.addEventListener('scroll', () => onScroll({ top: view.scrollDOM.scrollTop, left: view.scrollDOM.scrollLeft }));
    }}/ >;
}
