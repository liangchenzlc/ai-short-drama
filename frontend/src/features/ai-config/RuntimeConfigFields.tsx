import { useState } from 'react';
import { aiConfigTools } from '../../api/modules/ai-config-tools';
import { errorMessage } from '../../api/http';
import type { ProtocolProviderDto } from '../../api/types/ai-config-tools';
import type { RuntimeDraft, ServiceType } from './config-model';

export function RuntimeConfigFields({ value, serviceType, onChange, readOnly }: {
  value: RuntimeDraft; serviceType: ServiceType; onChange: (value: RuntimeDraft) => void; readOnly: boolean;
}) {
  const [providers, setProviders] = useState<ProtocolProviderDto[]>([]);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');
  const patch = (next: Partial<RuntimeDraft>) => onChange({ ...value, ...next });
  async function loadProtocols() {
    if (state === 'loading') return;
    setState('loading'); setError('');
    try { setProviders(await aiConfigTools.protocols(serviceType)); setState('ready'); }
    catch (cause) { setError(errorMessage(cause)); setState('error'); }
  }
  const selected = providers.find(provider => provider.id === value.protocol);
  const jsonFields = [
    ['capabilityConfig', '能力配置 JSON', '填写模型实际支持的输入、输出和参数限制；未知时留空。'],
    ['defaultOptions', '默认参数 JSON', '填写此模型的默认生成参数；留空沿用现有默认值。'],
    ['logicalCapabilitySpec', '逻辑能力规范 JSON', '可选的完整能力规范，格式为 JSON 对象。'],
    ['logicalCapabilityProfiles', '逻辑能力配置集 JSON', '可选的能力规范集合，格式为 JSON 对象数组。'],
  ] as const;
  return <details className="config-advanced" onToggle={event => {
    if (event.currentTarget.open && state === 'idle') void loadProtocols();
  }}>
    <summary>高级配置{value.enabled ? ' · 已启用' : ''}</summary>
    <div className="config-advanced-body">
      {readOnly && <p className="config-default-hint">官方连接的协议与能力由服务端目录维护。</p>}
      <label className="form-check"><input type="checkbox" checked={value.enabled} disabled={readOnly} onChange={event => patch({ enabled: event.target.checked })}/>使用指定协议与模型参数</label>
      <fieldset disabled={!value.enabled || readOnly}>
        <div className="config-field-pair">
          <label>API 格式<select value={value.apiFormat} onChange={event => patch({ apiFormat: event.target.value as RuntimeDraft['apiFormat'] })}>
            <option value="openai">OpenAI 兼容</option><option value="gemini">Google Gemini</option><option value="claude">Claude</option>
          </select></label>
          <label htmlFor="config-protocol">请求协议<input id="config-protocol" list="config-protocol-options" value={value.protocol} maxLength={120} onChange={event => patch({ protocol: event.target.value })} placeholder="选择或填写协议"/></label>
          <datalist id="config-protocol-options">{providers.filter(provider => provider.enabled && !provider.unavailableReason).map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</datalist>
        </div>
        {state === 'loading' && <p role="status" className="config-default-hint">正在读取协议目录…</p>}
        {error && <p role="alert" className="form-error">{error} <button type="button" onClick={() => void loadProtocols()}>重新读取协议目录</button></p>}
        {selected?.unavailableReason && <p role="alert" className="form-error">当前协议尚不可执行：{selected.unavailableReason}</p>}
        <label>参考资源源站<input type="url" value={value.referenceAssetOrigin} maxLength={2048} onChange={event => patch({ referenceAssetOrigin: event.target.value })} placeholder="选填，例如 https://media.example.com"/></label>
        {jsonFields.map(([key, label, hint]) => <label key={key}>{label}<textarea className="config-json-editor" rows={4} spellCheck={false} value={value[key]} onChange={event => patch({ [key]: event.target.value })} placeholder={key === 'logicalCapabilityProfiles' ? '[]' : '{}'}/><span className="config-default-hint">{hint}</span></label>)}
        <div className="config-field-pair">
          <label>视频能力版本<input value={value.videoCapabilitiesVersion} maxLength={128} onChange={event => patch({ videoCapabilitiesVersion: event.target.value })} placeholder="可选"/></label>
          <label>并发上限<input type="number" min={1} max={1024} step={1} value={value.concurrencyLimit} onChange={event => patch({ concurrencyLimit: event.target.value })} placeholder="沿用服务端设置"/></label>
        </div>
      </fieldset>
    </div>
  </details>;
}
