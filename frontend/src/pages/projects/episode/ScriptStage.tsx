import type { ReactNode } from 'react';
import { Button } from 'antd';
import { Download } from 'lucide-react';
import type { EditorDocument } from '../../../api/modules/episode-writing';
import type { ScriptDetail } from '../../../api/modules/storyboard';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { QuillScriptEditor } from '../../../features/projects/QuillScriptEditor';

export function ScriptStage({ script, editorDocument, readOnly, busy, onEdit, actions, candidate, showingCandidate = false, onShowCandidate, onAdopt, message }: {
  value: EpisodeWorkflow; script: string; editorDocument?: EditorDocument | null;
  confirmed: boolean; readOnly: boolean; busy: boolean;
  onEdit: (content: string, document: EditorDocument) => void; onConfirm: () => void;
  onContinue: () => void; projectAspect?: '16:9' | '9:16';
  actions?: ReactNode; candidate?: ScriptDetail | null; showingCandidate?: boolean;
  onShowCandidate?: (show: boolean) => void; onAdopt?: () => void; message?: ReactNode;
}) {
  const preview = showingCandidate && candidate;
  const switches = candidate ? <div className="novel-document-tabs" aria-label="剧本版本">
    <Button type="text" size="small" aria-pressed={!preview} onClick={() => onShowCandidate?.(false)}>当前稿</Button>
    <Button type="text" size="small" aria-pressed={!!preview} onClick={() => onShowCandidate?.(true)}>最新生成</Button>
  </div> : null;
  function download() {
    const url = URL.createObjectURL(new Blob([preview ? preview.content : script], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = preview ? '候选剧本.txt' : '本集剧本.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return <section className="novel-script-panel" aria-label="本集剧本编辑">
    <header className="novel-script-heading"><h2>本集剧本</h2><div className="novel-script-heading-actions">
      {preview && <span className="status-badge is-pending">新结果 · 未采用</span>}
      <Button type="text" size="small" icon={<Download size={15}/>} onClick={download} disabled={!script && !preview}>导出</Button>
      {actions}
    </div></header>
    {message}
    <div className="novel-current-document" hidden={!!preview}>
      <QuillScriptEditor content={script} document={editorDocument} readOnly={readOnly} onChange={onEdit} toolbarActions={switches}/>
    </div>
    {preview && <QuillScriptEditor key={preview.id} content={preview.content} readOnly label="新剧本结果预览" toolbarActions={switches}/>}
    {preview && <footer className="novel-script-result"><strong>新剧本 · 未采用</strong><Button type="primary" aria-label="采用并编辑" loading={busy} disabled={readOnly || busy} onClick={onAdopt}>采用并编辑</Button></footer>}
  </section>;
}
