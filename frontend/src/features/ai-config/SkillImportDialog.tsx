import { useRef, useState } from 'react';
import { Button, Skeleton } from 'antd';
import { Upload } from 'lucide-react';
import { agentsApi } from '../../api/modules/agents';
import type { AgentSkillContent, AgentSkillImportPreview } from '../../api/types/agents';
import { errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';

export function SkillImportDialog({ onClose, onImport }: { onClose: () => void; onImport: (content: AgentSkillContent) => void }) {
  const [busy, setBusy] = useState(false);
  return <Dialog title="导入 Skill" className="skill-import-dialog" onClose={onClose} canClose={!busy}>
    <ImportBody busy={busy} onBusy={setBusy} onClose={onClose} onImport={onImport}/>
  </Dialog>;
}
function ImportBody({ busy, onBusy, onClose, onImport }: {
  busy: boolean; onBusy: (value: boolean) => void; onClose: () => void; onImport: (content: AgentSkillContent) => void;
}) {
  const [preview, setPreview] = useState<AgentSkillImportPreview | null>(null);
  const [issue, setIssue] = useState('');
  const fileInput = useRef<HTMLInputElement>(null); const lock = useRef(false);
  async function inspect(file: File) {
    if (lock.current) return;
    if (!/\.(md|zip)$/i.test(file.name) || !file.size || file.size > 4 * 1048576) { setIssue('请选择非空 Markdown 或 ZIP 文件，最多 4 MiB。'); return; }
    lock.current = true; onBusy(true); setIssue(''); setPreview(null);
    try { setPreview(await agentsApi.previewSkillImport(file)); }
    catch (cause) { setIssue(`导入预览失败：${errorMessage(cause)}`); }
    finally { lock.current = false; onBusy(false); }
  }
  const unsupported = preview?.files.some(item => !item.supported);
  return <div className="skill-import-body">
    <p>单个 Markdown 或包含 SKILL.md 的 ZIP。先检查内容，再进入编辑；不会覆盖同名 Skill。</p>
    <Button icon={<Upload size={15}/>} disabled={busy} loading={busy} onClick={() => fileInput.current?.click()}>{preview ? '重新选择文件' : '选择 Markdown 或 ZIP'}</Button>
    <input ref={fileInput} hidden type="file" accept=".md,.zip,text/markdown,application/zip" aria-label="导入 Skill 文件"
      onChange={event => { const file = event.target.files?.[0]; event.currentTarget.value = ''; if (file) void inspect(file); }}/>
    {issue && <StudioFeedback type="error" message={issue} noticeKey="skill-import-preview" persistent actions={<Button onClick={() => fileInput.current?.click()}>重新选择文件</Button>}/>}
    {busy ? <div role="status" aria-label="正在检查技能包"><Skeleton active paragraph={{ rows: 4 }}/></div>
      : preview && <section aria-labelledby="skill-import-result"><h3 id="skill-import-result">{preview.filename}</h3>
        <dl><dt>名称</dt><dd>{preview.draft.name}</dd><dt>用途</dt><dd>{preview.draft.description || '进入编辑后补充用途'}</dd>
          <dt>文件</dt><dd>{preview.files.length} 份 · {(preview.byte_size / 1024).toFixed(1)} KiB</dd>
          <dt>兼容性</dt><dd>{preview.compatible ? '支持的平台方法包' : '需要修正'}</dd></dl>
        {preview.issues.length > 0 && <div className="skill-import-issues"><h4>需要处理的问题</h4><ul>{preview.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>
          {unsupported && <p>首期不支持脚本和二进制资料。请移除这些依赖，重新导入可独立使用的方法包。</p>}</div>}
        <ul className="skill-import-files" aria-label="导入文件目录">{preview.files.map(item => <li key={item.path}><span>{item.path}</span><small>{item.supported ? `${item.byte_size} B` : '不支持'}</small></li>)}</ul>
        <details><summary>主指令预览</summary><pre>{preview.draft.instructions}</pre></details>
      </section>}
    <footer className="dialog-actions"><Button disabled={busy} onClick={onClose}>取消</Button>
      <Button type="primary" disabled={!preview || busy || unsupported} onClick={() => preview && onImport(preview.draft)}>进入编辑</Button></footer>
  </div>;
}
