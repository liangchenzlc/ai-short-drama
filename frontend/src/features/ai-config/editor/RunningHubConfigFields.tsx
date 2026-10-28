import { aiConfigTools } from '@host/api/ai-config/tools';
import { errorMessage, isCancelled } from '@host/api/http';
import type { AiConfig, RuntimeDraft } from '@host/types/ai-config/editor/config-model';
import { useEffect, useRef, useState } from 'react';
export function RunningHubConfigFields({
  value,
  saved,
  connectionSaved,
  onChange,
  onBusy,
}: {
  value: RuntimeDraft;
  saved: AiConfig | null;
  connectionSaved: boolean;
  onChange: (value: RuntimeDraft) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const patch = (next: Partial<RuntimeDraft>) => onChange({ ...value, ...next });
  const previous = saved?.runtimeProfile?.runninghub_workflow;
  const savedId =
    previous?.kind === 'app'
      ? previous.webapp_id || previous.workflow_id || saved?.modelKey
      : previous?.workflow_id || saved?.modelKey;
  const canRead =
    !!saved &&
    connectionSaved &&
    saved.runtimeProfile?.protocol === value.protocol &&
    (previous?.kind ?? 'workflow') === value.runningHubKind &&
    (value.runningHubId.trim() || saved.modelKey) === savedId;
  async function readFields() {
    if (!saved || !canRead || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    onBusy(true);
    setError('');
    setNotice('');
    try {
      const descriptor = await aiConfigTools.runningHubSchema(saved.id, saved.rowVersion, controller.signal);
      if (!controller.signal.aborted) {
        patch({
          runningHubKind: descriptor.kind,
          runningHubId: descriptor.kind === 'app' ? descriptor.webapp_id : descriptor.workflow_id,
          runningHubTitle: value.runningHubTitle || descriptor.title,
          runningHubFields: JSON.stringify(descriptor.fields, null, 2),
          runningHubJson: JSON.stringify(descriptor.workflow_json, null, 2),
        });
        setNotice(
          descriptor.fields.length
            ? `已读取 ${descriptor.fields.length} 个字段，请核对后保存配置。`
            : '该条目没有公开字段；可保存空映射。',
        );
      }
    } catch (cause) {
      if (!controller.signal.aborted && !isCancelled(cause)) setError(errorMessage(cause));
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) {
        setLoading(false);
        onBusy(false);
      }
    }
  }
  return (
    <div className="config-advanced-body">
      <div className="config-field-pair">
        <label>
          RunningHub 条目类型
          <select
            value={value.runningHubKind}
            onChange={(event) =>
              patch({
                runningHubKind: event.target.value as RuntimeDraft['runningHubKind'],
                runningHubFields: '',
                runningHubJson: '',
              })
            }
          >
            <option value="workflow">Workflow 工作流</option>
            <option value="app">AI App 应用</option>
          </select>
        </label>
        <label>
          {value.runningHubKind === 'app' ? 'App ID' : 'Workflow ID'}
          <input
            value={value.runningHubId}
            maxLength={256}
            onChange={(event) => patch({ runningHubId: event.target.value })}
            placeholder="留空使用模型标识"
          />
        </label>
      </div>
      <label>
        画布条目名称
        <input
          value={value.runningHubTitle}
          maxLength={120}
          onChange={(event) => patch({ runningHubTitle: event.target.value })}
          placeholder="留空使用条目 ID"
        />
      </label>
      <button type="button" disabled={!canRead || loading} onClick={() => void readFields()}>
        {loading ? '正在读取工作流字段…' : '读取工作流字段'}
      </button>
      {!canRead && <p className="config-default-hint">先保存服务地址、积分 API Key、协议及条目 ID，再读取字段。</p>}
      <p className="config-default-hint">
        读取字段只查询结构。积分 API Key 用于查询、提交和轮询；第二密钥用于企业级参考素材上传。
      </p>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="config-default-hint">
          {notice}
        </p>
      )}
      <label>
        工作流字段映射 JSON
        <textarea
          className="config-json-editor"
          rows={6}
          spellCheck={false}
          value={value.runningHubFields}
          onChange={(event) => patch({ runningHubFields: event.target.value })}
          placeholder="[]"
        />
      </label>
      <label>
        工作流结构 JSON
        <textarea
          className="config-json-editor"
          rows={4}
          spellCheck={false}
          value={value.runningHubJson}
          onChange={(event) => patch({ runningHubJson: event.target.value })}
          placeholder="{}"
        />
      </label>
    </div>
  );
}
