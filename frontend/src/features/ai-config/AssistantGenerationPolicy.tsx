import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Switch } from 'antd';
import { assistantApi } from '../../api/modules/assistant';
import type { AssistantGenerationPolicy as Policy } from '../../api/types/assistant';
import { ApiError, errorMessage, isCancelled } from '../../api/http';
import { useStudioMessage } from '../../components/ui/StudioFeedback';
import './config-form.css';

export function AssistantGenerationPolicy({ active }: { active: boolean }) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [draft, setDraft] = useState(true);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [issue, setIssue] = useState('');
  const [blocked, setBlocked] = useState(false);
  const request = useRef(0);
  const live = useRef(true);
  const message = useStudioMessage();
  useEffect(() => { live.current = true; return () => { live.current = false; request.current += 1; }; }, []);

  const read = useCallback(async (keepDraft: boolean, signal?: AbortSignal) => {
    const sequence = ++request.current;
    setLoading(true);
    try {
      const next = await assistantApi.generationPolicy(signal);
      if (!live.current || signal?.aborted || sequence !== request.current) return;
      setPolicy(next); if (!keepDraft) setDraft(next.media_generation_enabled);
      setIssue(''); setBlocked(false);
    } catch (cause) {
      if (!live.current || signal?.aborted || isCancelled(cause) || sequence !== request.current) return;
      setIssue(`读取助手生成设置失败：${errorMessage(cause)}`);
    } finally {
      if (live.current && sequence === request.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active || policy) return;
    const controller = new AbortController();
    void read(false, controller.signal);
    return () => controller.abort();
  }, [active, policy, read]);

  async function save() {
    if (!policy || loading || saving || blocked) return;
    const enabled = draft;
    setSaving(true); setIssue('');
    try {
      const next = await assistantApi.updateGenerationPolicy(enabled, policy.policy_version);
      if (!live.current) return;
      setPolicy(next); setDraft(next.media_generation_enabled);
      message.success('助手生成设置已保存。', 'assistant-generation-policy-saved');
    } catch (cause) {
      if (!live.current) return;
      const conflict = cause instanceof ApiError && cause.status === 409;
      const unknown = !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;
      const text = conflict ? '设置已在其他窗口变化。你的选择已保留，请读取最新状态后核对。'
        : unknown ? '保存结果待核对。你的选择已保留，请读取最新状态。'
        : `设置未保存：${errorMessage(cause)}`;
      setBlocked(conflict || unknown); setIssue(text);
      message.error(text, 'assistant-generation-policy-save');
    } finally { if (live.current) setSaving(false); }
  }

  const dirty = !!policy && draft !== policy.media_generation_enabled;
  return <section aria-labelledby="assistant-policy-title">
    <div className="config-connection">
      <div className="config-connection-info">
        <strong id="assistant-policy-title">允许生成图片、视频、音频</strong>
        <p className="muted">仅影响助手按本次要求发起的媒体生成，讨论、提示词和流程搭建照常使用。</p>
        <p className="muted">关闭后，已经外发的任务仍会完成并保存结果。各类媒体仍需配置可用模型。</p>
      </div>
      <Switch aria-labelledby="assistant-policy-title" checked={!!policy && draft} loading={loading || saving}
        disabled={!policy || loading || saving || blocked} onChange={setDraft} />
    </div>
    <div className="config-connection">
      <div className="config-connection-info" aria-live="polite">
        {issue ? <p role={policy ? 'status' : 'alert'}>{issue}</p>
          : <p>{loading ? '正在读取设置…' : !policy ? '尚未读取设置' : saving ? '正在保存…' : dirty ? '选择尚未保存' : `当前已保存：${policy.media_generation_enabled ? '允许生成' : '关闭生成'}`}</p>}
        {issue && policy && <p>本次选择：{draft ? '允许生成' : '关闭生成'} · 当前读取的保存状态：{policy.media_generation_enabled ? '允许生成' : '关闭生成'}</p>}
      </div>
      <div className="config-connection-actions">
        <Button disabled={loading || saving} onClick={() => void read(dirty || blocked)}>读取最新状态</Button>
        <Button type="primary" aria-label="保存设置" aria-busy={saving} loading={saving}
          disabled={!dirty || loading || blocked} onClick={() => void save()}>保存设置</Button>
      </div>
    </div>
  </section>;
}
