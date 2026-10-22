import { useEffect, useRef, useState } from 'react';
import { Button, Skeleton } from 'antd';
import { FileText, Plus, Trash2 } from 'lucide-react';
import { agentsApi } from '../../api/modules/agents';
import { errorMessage } from '../../api/http';
import type { SkillResourceDraft } from './skill-draft';

export async function readSkillResourceText(id: string, resource: SkillResourceDraft, signal?: AbortSignal): Promise<string> {
  if (resource.content !== null) return resource.content;
  if (!resource.origin_version || !resource.checksum_sha256) throw new Error('资料版本缺失，请重新读取 Skill。');
  let offset = 0; let result = '';
  while (true) {
    const part = await agentsApi.skillResource(id, resource.origin_version, resource.path, offset, signal);
    if (part.skill_id !== id || part.content_version !== resource.origin_version || part.path !== resource.path
      || part.checksum_sha256 !== resource.checksum_sha256 || part.offset !== offset
      || part.content.length > 6000 || result.length + part.content.length > 131072
      || part.next_offset !== null && (part.next_offset !== offset + part.content.length || part.next_offset <= offset)) {
      throw new Error('资料响应与冻结版本不一致，请重新读取。');
    }
    result += part.content;
    if (part.next_offset === null) return result;
    offset = part.next_offset;
  }
}

export function SkillResources({ skillId, resources, readOnly, disabled, onChange, onCache }: {
  skillId?: string; resources: SkillResourceDraft[]; readOnly: boolean; disabled: boolean;
  onChange: (value: SkillResourceDraft[]) => void; onCache: (resource: SkillResourceDraft, content: string) => void;
}) {
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [issue, setIssue] = useState('');
  const [revision, setRevision] = useState(0);
  const cache = useRef(onCache); cache.current = onCache;
  const current = resources[selected];
  useEffect(() => {
    if (!current || current.content !== null || !skillId) { setLoading(false); setIssue(''); return; }
    const controller = new AbortController(); setLoading(true); setIssue('');
    readSkillResourceText(skillId, current, controller.signal).then(content => {
      if (!controller.signal.aborted) cache.current(current, content);
    }).catch(cause => { if (!controller.signal.aborted) setIssue(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [skillId, current?.path, current?.origin_version, current?.content === null, revision]);
  function update(patch: Partial<SkillResourceDraft>) {
    onChange(resources.map((item, index) => index === selected ? { ...item, ...patch } : item));
  }
  function add(kind: 'reference' | 'template') {
    const prefix = kind === 'reference' ? 'references/notes' : 'assets/template';
    let index = resources.length + 1;
    while (resources.some(item => item.path === `${prefix}-${index}.md`)) index++;
    setSelected(resources.length); onChange([...resources, { path: `${prefix}-${index}.md`, kind, content: '' }]);
  }
  return <section className="skill-resource-section" aria-labelledby="skill-resources-title">
    <div className="skill-section-heading"><div><h3 id="skill-resources-title">参考资料与模板</h3><p>仅在需要时读取，保存后随内容版本保留。</p></div>
      {!readOnly && <div className="skill-actions"><Button icon={<Plus size={14}/>} disabled={disabled || resources.length >= 31} onClick={() => add('reference')}>添加资料</Button>
        <Button disabled={disabled || resources.length >= 31} onClick={() => add('template')}>添加模板</Button></div>}
    </div>
    {!resources.length ? <p className="skill-resource-empty">{readOnly ? '此 Skill 没有附加资料。' : '还没有附加资料。简短的方法可以只保留主指令。'}</p>
      : <div className="skill-files">
        <nav aria-label="Skill 资料文件">{resources.map((resource, index) => <button type="button" key={index}
          aria-current={selected === index ? 'true' : undefined} onClick={() => setSelected(index)}>
          <FileText size={15}/><span>{resource.path}<small>{resource.kind === 'template' ? '模板' : '参考资料'}</small></span>
        </button>)}</nav>
        <div className="skill-file-editor">
          {current && <>
            <div className="skill-file-heading"><label>相对路径<input value={current.path} readOnly={readOnly} disabled={disabled || current.content === null}
              maxLength={255} onChange={event => update({ path: event.target.value })}/></label>
              <label>资料类型<select value={current.kind} disabled={readOnly || disabled} onChange={event => update({ kind: event.target.value as 'reference' | 'template' })}>
                <option value="reference">参考资料</option><option value="template">模板</option></select></label>
              {!readOnly && <Button type="text" danger aria-label={`移除资料 ${current.path}`} icon={<Trash2 size={16}/>} disabled={disabled}
                onClick={() => { onChange(resources.filter((_, index) => index !== selected)); setSelected(Math.max(0, selected - 1)); }}/>}</div>
            {loading ? <div role="status" aria-label="正在读取资料"><Skeleton active paragraph={{ rows: 5 }}/></div>
              : issue ? <div className="skill-read-error" role="alert"><p>{issue}</p><Button onClick={() => setRevision(value => value + 1)}>重新读取资料</Button></div>
              : <label className="skill-file-content">资料正文<textarea aria-label={`资料正文 ${current.path}`} rows={12} value={current.content ?? ''}
                readOnly={readOnly} disabled={disabled || current.content === null} maxLength={131072}
                onChange={event => update({ content: event.target.value })}/></label>}
          </>}
        </div>
      </div>}
  </section>;
}
