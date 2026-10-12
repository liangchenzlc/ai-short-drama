import { useEffect, useRef, useState } from 'react';
import { Button, Switch } from 'antd';
import { canvasEditorTools, type CanvasEditorToolId, type CanvasEditorToolStatuses } from '../../api/modules/canvas-editor-tools';
import { errorMessage, isCancelled } from '../../api/http';

const tools: { id: CanvasEditorToolId; label: string; description: string }[] = [
  { id: 'ai-art-critique', label: 'AI 审美分析', description: '在画布中分析图片的构图、色彩、光照与结构。' },
  { id: 'runninghub-workflow-provider', label: 'RunningHub 工作流', description: '在画布中使用本人保存的 Workflow 与 AI App 生成图片、视频和音频。' },
];

export function CanvasEditorTools() {
  const [value, setValue] = useState<CanvasEditorToolStatuses | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<CanvasEditorToolId | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function reload() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true); setError(''); setNotice('');
    try {
      const next = await canvasEditorTools.read(controller.signal);
      if (!controller.signal.aborted) setValue(next);
    } catch (cause) {
      if (!controller.signal.aborted && !isCancelled(cause)) setError(errorMessage(cause));
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  async function toggle(id: CanvasEditorToolId, enabled: boolean) {
    if (!value || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(id); setError(''); setNotice('');
    try {
      const state = await canvasEditorTools.setEnabled(id, enabled, controller.signal);
      if (!controller.signal.aborted) {
        setValue(current => current ? { states: { ...current.states, [id]: state },
          statuses: { ...current.statuses, [id]: state.effectiveEnabled ? 'enabled' : 'disabled' } } : current);
        setNotice(`${tools.find(tool => tool.id === id)?.label}${state.effectiveEnabled ? '已启用' : '已关闭'}。`);
      }
    } catch (cause) {
      if (!controller.signal.aborted && !isCancelled(cause)) setError(errorMessage(cause));
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  return <details className="config-advanced config-editor-tools" onToggle={event => {
    if (event.currentTarget.open && !value && !request.current) void reload();
  }}>
    <summary>画布工具</summary>
    <div className="config-advanced-body">
      <p className="config-default-hint">仅影响本人，使用本人已保存的模型或工作流配置。启用开关不会发起模型调用。</p>
      {loading && <p role="status">正在读取工具状态…</p>}
      {error && <div role="alert" className="form-error"><p>{error}</p><Button onClick={() => void reload()} disabled={loading || !!busy}>重新读取工具状态</Button></div>}
      {value && <div className="config-editor-tool-list">
        {tools.map(tool => <div key={tool.id} className="config-editor-tool-row">
          <div><label id={`canvas-tool-${tool.id}`}>{tool.label}</label><p className="config-default-hint">{tool.description}</p></div>
          <Switch aria-labelledby={`canvas-tool-${tool.id}`} checked={value.states[tool.id].effectiveEnabled}
            disabled={loading || !!busy || !value.states[tool.id].canToggle} loading={busy === tool.id}
            onChange={enabled => void toggle(tool.id, enabled)}/>
        </div>)}
      </div>}
      {notice && <p role="status">{notice}</p>}
    </div>
  </details>;
}
