import { useState, type FormEvent } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { ApiError, errorMessage } from '../../api/http';
import { emptyConfig, serviceLabels, type AiConfig, type ConfigDraft, type ServiceType } from './config-model';
import { applyPreset, providerPresets } from './provider-presets';
import { ModelIdentifierField } from './ModelIdentifierField';

export function ConfigForm({ existing, serviceType, onClose, onSave, onReload }: {
  existing: AiConfig | null;
  serviceType: ServiceType;
  onClose: () => void;
  onSave: (draft: ConfigDraft) => Promise<void>;
  onReload: () => Promise<void>;
}) {
  const [form, setForm] = useState<ConfigDraft>(existing
    ? { ...existing, apiKey: '', clearApiKey: false } : { ...emptyConfig, serviceType });
  const [initial] = useState(() => JSON.stringify(form));
  function requestClose() {
    if (pending || (JSON.stringify(form) !== initial && !window.confirm('关闭会放弃尚未保存的配置修改，确定关闭？'))) return;
    onClose();
  }
  const [error, setError] = useState('');
  const [fields, setFields] = useState<string[]>([]);
  const [conflict, setConflict] = useState(false);
  const [pending, setPending] = useState(false);
  const change = (partial: Partial<ConfigDraft>) => setForm((value) => ({ ...value, ...partial }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || conflict) return;
    if (!form.name.trim() || !form.provider.trim() || !form.modelKey.trim()) {
      setError('请填写名称、提供商和模型标识。'); return;
    }
    setPending(true); setError(''); setFields([]);
    try { await onSave(form); }
    catch (cause) {
      setError(errorMessage(cause));
      if (cause instanceof ApiError) {
        setConflict(cause.status === 409);
        setFields(cause.fields.map((field) => field.message));
      }
    } finally { setPending(false); }
  }
  async function reload() {
    setPending(true);
    try { await onReload(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setPending(false); }
  }
  return <Dialog title={`${existing ? '编辑' : '添加'}${serviceLabels[serviceType]}`} className="model-config-dialog" onClose={requestClose} canClose={!pending}>
    <form className="studio-form ai-config-form" onSubmit={submit}>
      <fieldset disabled={pending}>
        <label>配置名称<input autoFocus value={form.name} onChange={(e) => change({ name: e.target.value })} required maxLength={120} placeholder="例如：故事创作模型" /></label>
        <label>快速填入厂商设置<select value="" onChange={(event) => {
          const preset = providerPresets[form.serviceType].find((item) => item.id === event.target.value);
          if (preset) change(applyPreset(preset));
        }}>
          <option value="">选择后填入服务地址和模型标识</option>
          {providerPresets[form.serviceType].map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
        </select></label>
        <label>厂商名称<input value={form.provider} onChange={(e) => change({ provider: e.target.value })} required maxLength={120} placeholder="选择预设或输入自定义厂商" /></label>
        <label>服务地址（Base URL）<input value={form.baseUrl} onChange={(e) => change({ baseUrl: e.target.value })} type="url" maxLength={2048} placeholder="选填，例如 https://api.example.com/v1" /></label>
        <label>API 密钥<input type="password" value={form.apiKey} disabled={form.clearApiKey} onChange={(e) => change({ apiKey: e.target.value })} autoComplete="new-password" placeholder={existing?.hasApiKey ? '已保存密钥；留空保留原密钥' : '选填，输入后保存到服务端'} /></label>
        {existing?.hasApiKey && <label className="form-check"><input type="checkbox" checked={form.clearApiKey} onChange={(e) => change({ clearApiKey: e.target.checked, apiKey: '' })} />清除已保存的密钥</label>}
        <ModelIdentifierField form={form} existing={existing} disabled={pending} onChange={(modelKey) => change({ modelKey })} />
        <label className="form-check"><input type="checkbox" checked={form.enabled} onChange={(e) => change({ enabled: e.target.checked })} />启用此配置</label>
      </fieldset>
      {error && <div role="alert" className="form-error"><p>{error}</p>{fields.map((field) => <p key={field}>{field}</p>)}</div>}
      {conflict && <div className="config-conflict"><p>重新加载会替换当前填写内容，请先保留需要的修改。</p><button type="button" onClick={reload} disabled={pending}>重新加载最新配置</button></div>}
      <div className="dialog-actions">
        <button type="button" onClick={requestClose} disabled={pending}>取消</button>
        <button className="studio-primary" type="submit" disabled={pending || conflict}>{pending ? '处理中…' : '保存配置'}</button>
      </div>
    </form>
  </Dialog>;
}
