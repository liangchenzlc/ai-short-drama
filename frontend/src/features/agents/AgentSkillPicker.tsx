import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Segmented, Skeleton } from 'antd';
import { Dialog } from '../../components/ui/Dialog';
import { confirmAction } from '../../components/ui/confirm';
import { agentsApi } from '../../api/modules/agents';
import { errorMessage } from '../../api/http';
import type { AgentSkill } from '../../api/types/agents';

export function AgentSkillPicker({ selected, disabled, onChange, onClose }: {
  selected: AgentSkill[]; disabled: boolean; onChange: (skills: AgentSkill[]) => void; onClose: () => void;
}) {
  const [items, setItems] = useState<AgentSkill[]>([]);
  const [tab, setTab] = useState<'builtin' | 'personal'>('builtin');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState<AgentSkill | null>(null);
  const [name, setName] = useState('');
  const [instructions, setInstructions] = useState('');
  const upload = useRef<HTMLInputElement>(null);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    (async () => {
      const all: AgentSkill[] = []; let total = 1;
      while (all.length < total) {
        const page = await agentsApi.skills(all.length, controller.signal);
        all.push(...page.items); total = page.total;
        if (!page.items.length) break;
      }
      if (!controller.signal.aborted) setItems(all);
    })().catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);
  async function mutate(operation: () => Promise<void>) {
    if (lock.current || disabled) return;
    lock.current = true; setBusy(true); setError('');
    try { await operation(); }
    catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  function replace(next: AgentSkill) {
    if (!alive.current) return;
    setItems(previous => previous.map(item => item.id === next.id ? next : item));
    if (selected.some(item => item.id === next.id)) onChange(next.enabled ? selected.map(item => item.id === next.id ? next : item) : selected.filter(item => item.id !== next.id));
  }
  const visible = items.filter(item => item.builtin === (tab === 'builtin'));
  const dirty = !!editing && !editing.builtin && (name !== editing.name || instructions !== editing.instructions);
  async function discardEditor() {
    return !dirty || await confirmAction('技能修改尚未保存，放弃本次编辑？', { title: '未保存的 Skill', confirmText: '放弃编辑' });
  }
  async function close() { if (!busy && await discardEditor()) onClose(); }
  return <Dialog title="加载 Skill" className="agent-context-dialog" canClose={!busy} onClose={() => void close()}>
    <div className="agent-context-dialog-body">
      <div className="agent-skill-heading"><Segmented aria-label="技能来源" value={tab} disabled={busy} options={[{ value: 'builtin', label: '内置技能' }, { value: 'personal', label: '我的技能' }]} onChange={value => setTab(value as typeof tab)}/><span>已加载 {selected.length} / 8</span></div>
      {tab === 'personal' && <><Button aria-label="上传 Skill" disabled={disabled || busy} loading={busy} onClick={() => upload.current?.click()}>上传 Skill</Button><input hidden ref={upload} type="file" accept=".md,text/markdown" aria-label="上传 Skill 文件" onChange={event => {
        const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
        if (file) void mutate(async () => {
          if (!await discardEditor()) return;
          if (file.size > 64 * 1024) throw new Error('Skill 文件不能超过 64 KiB。');
          if (!file.name.toLowerCase().endsWith('.md')) throw new Error('请选择 UTF-8 编码的 Markdown 文件。');
          const next = await agentsApi.uploadSkill(file);
          if (alive.current) { setItems(previous => [next, ...previous.filter(item => item.id !== next.id)]); setEditing(null); }
        });
      }}/><p className="agent-context-help">UTF-8 Markdown，最多 64 KiB。更新会产生新版本，已开始的任务继续使用提交时的版本。</p></>}
      {error && <Alert type="error" message={error} action={<Button size="small" disabled={busy} onClick={() => setRevision(value => value + 1)}>重新载入</Button>}/>}
      {loading ? <Skeleton active paragraph={{ rows: 4 }}/>
        : visible.length ? <div className="agent-skill-list">{visible.map(skill => <article key={skill.id}>
          <Checkbox checked={selected.some(item => item.id === skill.id)} disabled={disabled || busy || !skill.enabled || selected.length >= 8 && !selected.some(item => item.id === skill.id)} onChange={event => onChange(event.target.checked ? [...selected, skill] : selected.filter(item => item.id !== skill.id))}>{skill.name}</Checkbox>
          <span>版本 {skill.content_version}{skill.enabled ? '' : '（已停用）'}</span>
          <div><Button size="small" disabled={busy} onClick={async () => { if (await discardEditor()) { setEditing(skill); setName(skill.name); setInstructions(skill.instructions); } }}>{skill.builtin ? '查看' : '编辑'}</Button>{!skill.builtin && <>
            <Button size="small" disabled={disabled || busy} onClick={() => void mutate(async () => replace(await agentsApi.updateSkill(skill.id, { row_version: skill.row_version!, enabled: !skill.enabled })))}>{skill.enabled ? '停用' : '启用'}</Button>
            <Button size="small" danger disabled={disabled || busy} onClick={() => void mutate(async () => {
              if (!await confirmAction(`删除“${skill.name}”？已发送消息和已开始任务仍保留原技能版本。`, { title: '删除 Skill', confirmText: '删除' })) return;
              await agentsApi.deleteSkill(skill.id, skill.row_version!);
              if (alive.current) { setItems(previous => previous.filter(item => item.id !== skill.id)); onChange(selected.filter(item => item.id !== skill.id)); if (editing?.id === skill.id) setEditing(null); }
            })}>删除</Button>
          </>}</div>
        </article>)}</div> : !error && <p className="agent-context-empty">{tab === 'personal' ? '还没有自定义技能，上传一份创作规范开始使用。' : '暂无内置技能。'}</p>}
      {editing && <form className="agent-skill-editor" onSubmit={event => { event.preventDefault(); void mutate(async () => {
        const next = await agentsApi.updateSkill(editing.id, { row_version: editing.row_version!, name: name.trim(), instructions });
        replace(next); if (alive.current) setEditing(null);
      }); }}>
        <label>技能名称<Input value={name} maxLength={120} readOnly={editing.builtin} disabled={busy} onChange={event => setName(event.target.value)}/></label>
        <label>Markdown 指令<Input.TextArea value={instructions} rows={9} maxLength={65536} readOnly={editing.builtin} disabled={busy} onChange={event => setInstructions(event.target.value)}/></label>
        <div>{!editing.builtin && <Button htmlType="submit" type="primary" aria-label="保存新版本" loading={busy} disabled={disabled || !dirty || !name.trim() || !instructions.trim()}>保存新版本</Button>}<Button disabled={busy} onClick={async () => { if (await discardEditor()) setEditing(null); }}>{editing.builtin ? '关闭查看' : '取消编辑'}</Button></div>
      </form>}
      <div className="agent-context-dialog-actions"><Button type="primary" disabled={busy} onClick={() => void close()}>完成</Button></div>
    </div>
  </Dialog>;
}
