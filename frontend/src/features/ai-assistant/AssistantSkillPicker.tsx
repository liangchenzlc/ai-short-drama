import { useEffect, useState } from 'react';
import { Button, Checkbox, Input, Skeleton } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { errorMessage } from '../../api/http';
import type { AgentSkill } from '../../api/types/agents';
import type { AssistantSkillActivation, TaskExecutionTarget } from '../../api/types/assistant';

export interface AssistantSkillChoice { id: string; content_version: string; name?: string }

export function AssistantSkillPicker({ selected, activations = [], mode, disabled, onChange }: {
  selected: AssistantSkillChoice[]; activations?: AssistantSkillActivation[]; mode?: TaskExecutionTarget['mode'];
  disabled: boolean; onChange: (value: AssistantSkillChoice[]) => void;
}) {
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AgentSkill[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    if (offset === 0) setItems([]);
    const timer = setTimeout(() => agentsApi.skills(offset, controller.signal, { query: query.trim() || undefined })
      .then(page => { if (!controller.signal.aborted) { setItems(previous => offset === 0 ? page.items : [...previous.filter(item => !page.items.some(next => next.id === item.id)), ...page.items]); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); }), 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, offset, revision]);
  function choose(skill: AgentSkill) {
    if (disabled || selected.length >= 8 && !selected.some(item => item.id === skill.id)) return;
    onChange([...selected.filter(item => item.id !== skill.id), { id: skill.id, name: skill.name, content_version: skill.content_version }]);
  }
  return <section className="assistant-skill-picker" aria-label="选择 Skill">
      <div className="assistant-skill-search">
        <Input autoFocus aria-label="搜索 Skill" placeholder="搜索名称或用途" value={query} maxLength={120} allowClear onChange={event => { setQuery(event.target.value); setOffset(0); }}/>
      </div>
      <div className="assistant-skill-scroll" aria-busy={loading}>
        {error && <div role="alert" className="agent-context-empty"><p>{error}</p><Button size="small" onClick={() => setRevision(value => value + 1)}>重新载入</Button></div>}
        {items.length > 0 && <div className="assistant-skill-options">{items.map(skill => {
            const chosen = selected.find(item => item.id === skill.id);
            const reason = !skill.enabled ? '已停用，不能新加载' : !skill.compatible ? skill.issues.join('；') || '此方法需要先适配' : mode && !skill.modes.includes(mode) ? '不适用于当前写入目标模式' : '';
            const changed = !!chosen && chosen.content_version !== skill.content_version;
            return <article key={skill.id}>
              <Checkbox checked={!!chosen} disabled={disabled || !chosen && (!!reason || selected.length >= 8)} onChange={event => event.target.checked ? choose(skill) : onChange(selected.filter(item => item.id !== skill.id))}>{skill.name}</Checkbox>
              {changed || activations.some(item => item.active && item.source === 'auto' && item.skill_id === skill.id)
                ? <span className="assistant-skill-version">{changed ? `当前任务使用 v${chosen.content_version}，目录已更新到 v${skill.content_version}` : '自动使用中'}</span>
                : null}
              <p>{skill.description}</p>
              {reason && <p className="assistant-skill-issue">{reason}</p>}
              {changed && <Button size="small" disabled={disabled || !!reason} onClick={() => choose(skill)}>改用 v{skill.content_version}</Button>}
            </article>;
          })}</div>}
        {loading && <Skeleton active title={false} paragraph={{ rows: 3 }}/>}
        {!loading && !error && !items.length && <p className="agent-context-empty">{query ? '没有匹配的方法，请调整搜索条件。' : '暂无方法，可前往管理页导入。'}</p>}
        {!error && items.length < total && <Button block size="small" disabled={loading} onClick={() => setOffset(value => value + 50)}>加载更多 Skill</Button>}
      </div>
      <footer className="assistant-skill-footer"><span>已选择 {selected.length} / 8{selected.length >= 8 ? '，移除后可继续添加' : '，可多选'}</span><a href="/skills" target="_blank" rel="noopener noreferrer">管理 Skill</a></footer>
  </section>;
}
