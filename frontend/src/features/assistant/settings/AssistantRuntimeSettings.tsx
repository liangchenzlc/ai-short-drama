import { MediaLimitFields } from '@host/features/assistant/settings/MediaLimitFields';
import { useAssistantSettings } from '@host/features/assistant/settings/useAssistantSettings';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import { Button, Input, InputNumber, Skeleton } from 'antd';
import './assistant-settings.css';

export function AssistantRuntimeSettings({ active }: { active: boolean }) {
  const state = useAssistantSettings(active);
  const { draft, saved } = state;
  return <section className="assistant-runtime-settings" aria-labelledby="assistant-runtime-title">
    <h3 id="assistant-runtime-title">助手指令与运行额度</h3>
    {state.issue && <StudioFeedback type="warning" persistent title="助手设置" message={state.issue} />}
    {!draft || !saved ? (state.loading ? <Skeleton active paragraph={{ rows: 3 }} /> : <p>尚未读取助手设置。</p>) : <>
      <label className="assistant-settings-instructions">
        <span>自定义指令</span>
        <Input.TextArea aria-label="自定义指令" rows={5} maxLength={16000} value={draft.custom_instructions ?? ''}
          placeholder={saved.default_instructions} onChange={event => state.edit({ custom_instructions: event.target.value })} />
      </label>
      <div className="assistant-settings-inline">
        <span className="muted">{draft.custom_instructions === null ? '使用系统默认指令' : '使用自定义指令'} · 下次执行生效</span>
        <Button size="small" disabled={draft.custom_instructions === null} onClick={() => state.edit({ custom_instructions: null })}>恢复默认指令</Button>
      </div>
      <details><summary>查看系统默认指令</summary><pre className="assistant-default-instructions">{saved.default_instructions}</pre></details>
      <div className="assistant-runtime-limit-fields">
        <label><span>模型调用上限</span><InputNumber aria-label="模型调用上限" min={20} max={Math.min(2000, saved.deployment_limits.model_call_limit)} precision={0} value={draft.model_call_limit} onChange={value => { if (value !== null) state.edit({ model_call_limit: value }); }} /></label>
        <label><span>输出 Token 额度</span><InputNumber aria-label="输出 Token 额度" min={100000} max={Math.min(20000000, saved.deployment_limits.output_token_limit)} precision={0} value={draft.output_token_limit} onChange={value => { if (value !== null) state.edit({ output_token_limit: value }); }} /></label>
        <label><span>子任务并发上限</span><InputNumber aria-label="子任务并发上限" min={1} max={Math.min(3, saved.deployment_limits.subagent_max_concurrency)} precision={0} value={draft.subagent_max_concurrency} onChange={value => { if (value !== null) state.edit({ subagent_max_concurrency: value }); }} /></label>
      </div>
      <p className="muted">模型调用、输出额度与子任务并发在下次执行生效，当前执行沿用已冻结的设置。</p>
      <h4>默认媒体数量</h4>
      <MediaLimitFields value={draft.default_media_limits} onChange={value => state.edit({ default_media_limits: value })} prefix="默认" />
      <p className="muted">新任务生效。留空表示不限，0 表示禁止该类生成；发送时可为本任务覆盖数量。</p>
    </>}
    <div className="assistant-settings-actions">
      <span className="muted" role="status">{state.saving ? '正在保存…' : state.dirty ? '有未保存的修改' : saved ? '设置已同步' : ''}</span>
      <Button disabled={state.loading || state.saving} onClick={() => void state.read()}>读取最新版本</Button>
      <Button type="primary" aria-label="保存运行设置" aria-busy={state.saving} loading={state.saving} disabled={!state.dirty || state.loading || state.blocked} onClick={() => void state.save()}>保存运行设置</Button>
    </div>
  </section>;
}
