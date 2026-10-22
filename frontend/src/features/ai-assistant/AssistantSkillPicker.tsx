import { useEffect, useState } from 'react';
import { Button, Checkbox, Input, Select, Skeleton } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { errorMessage } from '../../api/http';
import type { AgentSkill } from '../../api/types/agents';
import type { AssistantSkillActivation, TaskExecutionTarget } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';

export interface AssistantSkillChoice { id: string; content_version: string; name?: string }

export function AssistantSkillPicker({ selected, activations = [], mode, disabled, onChange, onClose }: {
  selected: AssistantSkillChoice[]; activations?: AssistantSkillActivation[]; mode?: TaskExecutionTarget['mode'];
  disabled: boolean; onChange: (value: AssistantSkillChoice[]) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<'all' | 'builtin' | 'personal'>('all');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AgentSkill[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setItems([]);
    const timer = setTimeout(() => agentsApi.skills(offset, controller.signal, { query: query.trim() || undefined, source: source === 'all' ? undefined : source })
      .then(page => { if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); }), 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, source, offset, revision]);
  function choose(skill: AgentSkill) {
    onChange([...selected.filter(item => item.id !== skill.id), { id: skill.id, name: skill.name, content_version: skill.content_version }]);
  }
  const automatic = activations.filter(item => item.active && item.source === 'auto' && !selected.some(choice => choice.id === item.skill_id));
  return <Dialog title="加载 Skill" className="ai-assistant-context-dialog standard-creation-dialog standard-assistant-dialog assistant-skill-picker" onClose={onClose}>
    <div className="agent-context-dialog-body">
      <p className="agent-context-help">方法随当前任务固定版本，补充消息继续使用。选择后不会立即执行。</p>
      <div className="assistant-skill-search">
        <Input aria-label="搜索 Skill" placeholder="搜索名称、用途或标签" value={query} maxLength={120} allowClear onChange={event => { setQuery(event.target.value); setOffset(0); }}/>
        <Select aria-label="技能来源" value={source} onChange={value => { setSource(value); setOffset(0); }} options={[{ value: 'all', label: '全部方法' }, { value: 'builtin', label: '内置方法' }, { value: 'personal', label: '我的方法' }]}/>
      </div>
      {selected.length > 0 && <div className="assistant-skill-selected" aria-label="手动指定方法">
        {selected.map(item => <div key={item.id}><span>{item.name ?? item.id} v{item.content_version}</span><Button size="small" type="text" disabled={disabled} onClick={() => onChange(selected.filter(choice => choice.id !== item.id))}>移除</Button></div>)}
      </div>}
      {automatic.length > 0 && <details className="assistant-skill-automatic"><summary>当前任务自动补充 {automatic.length} 项</summary>{automatic.map(item => <p key={item.activation_id}>{item.name} v{item.content_version} · {item.purpose}{item.issue ? ` · ${item.issue}` : ''}</p>)}</details>}
      {loading ? <Skeleton active paragraph={{ rows: 4 }}/>
        : error ? <div role="alert" className="agent-context-empty"><p>{error}</p><Button size="small" onClick={() => setRevision(value => value + 1)}>重新载入</Button></div>
          : items.length ? <div className="assistant-skill-options">{items.map(skill => {
            const chosen = selected.find(item => item.id === skill.id);
            const reason = !skill.enabled ? '已停用，不能新加载' : !skill.compatible ? skill.issues.join('；') || '此方法需要先适配' : mode && !skill.modes.includes(mode) ? '不适用于当前写入目标模式' : '';
            const changed = !!chosen && chosen.content_version !== skill.content_version;
            return <article key={skill.id}>
              <Checkbox checked={!!chosen} disabled={disabled || !chosen && (!!reason || selected.length >= 8)} onChange={event => event.target.checked ? choose(skill) : onChange(selected.filter(item => item.id !== skill.id))}>{skill.name}</Checkbox>
              <span className="assistant-skill-version">{changed ? `任务使用 v${chosen.content_version}；目录版本 v${skill.content_version}` : `版本 ${skill.content_version}`} · {skill.invocation_mode === 'manual' ? '仅手动' : '按需自动'}</span>
              <p>{skill.description}</p>
              {reason && <p className="assistant-skill-issue">{reason}</p>}
              {changed && <Button size="small" disabled={disabled || !!reason} onClick={() => choose(skill)}>改用 v{skill.content_version}</Button>}
            </article>;
          })}</div> : <p className="agent-context-empty">{query ? '没有匹配的方法，请调整搜索条件。' : '暂无方法，可前往管理页新建或导入。'}</p>}
      {!error && total > 50 && <div className="agent-context-pagination"><Button size="small" aria-label="上一页" disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页</Button><span>{offset + 1}–{Math.min(offset + 50, total)} / {total}</span><Button size="small" aria-label="下一页" disabled={loading || offset + 50 >= total} onClick={() => setOffset(value => value + 50)}>下一页</Button></div>}
    </div>
    <footer className="standard-assistant-dialog-footer"><span>已选择 {selected.length} / 8</span><a href="/ai_config?section=skills" target="_blank" rel="noopener noreferrer">管理 Skill</a><Button type="primary" onClick={onClose}>完成</Button></footer>
  </Dialog>;
}
