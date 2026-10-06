import { confirmAction } from '../../components/ui/confirm';
import { useEffect, useState, type FormEvent } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { ApiError, errorMessage } from '../../api/http';
import { serviceLabels, type AiConfig, type ConfigDraft, type ServiceType } from './config-model';
import { createConfigDraft, serializeConfigDraft } from './config-form-model';
import { RuntimeConfigFields } from './RuntimeConfigFields';
import { applyPreset, providerPresets } from './provider-presets';
import { ModelIdentifierField } from './ModelIdentifierField';
import './config-form.css';

export function ConfigForm({ existing, serviceType, onClose, onSave, onReload }: {
  existing: AiConfig | null;
  serviceType: ServiceType;
  onClose: () => void;
  onSave: (draft: ConfigDraft, makeDefault: boolean) => Promise<void>;
  onReload: () => Promise<void>;
}) {
  const [form, setForm] = useState<ConfigDraft>(() => createConfigDraft(existing, serviceType));
  const managed = existing?.credentialSource === 'beefapi';
  const [makeDefault, setMakeDefault] = useState(existing?.isDefault ?? false);
  const [initial] = useState(() => JSON.stringify(form));
  async function requestClose() {
    if (pending || ((JSON.stringify(form) !== initial || makeDefault !== (existing?.isDefault ?? false))
      && !await confirmAction('关闭会放弃尚未保存的配置修改，确定关闭？'))) return;
    onClose();
  }
  const [error, setError] = useState('');
  const [fields, setFields] = useState<string[]>([]);
  const [conflict, setConflict] = useState(false);
  const [pending, setPending] = useState(false);
  const dirty = JSON.stringify(form) !== initial || makeDefault !== (existing?.isDefault ?? false);
  useEffect(() => {
    if (!dirty && !pending) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty, pending]);
  const change = (partial: Partial<ConfigDraft>) => setForm((value) => ({ ...value, ...partial }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || conflict) return;
    if (!form.name.trim() || !form.provider.trim() || !form.modelKey.trim()) {
      setError('请填写名称、提供商和模型标识。'); return;
    }
    try { serializeConfigDraft(form); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '请核对高级配置。'); return; }
    setPending(true); setError(''); setFields([]);
    try { await onSave(form, form.enabled && makeDefault); }
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
        <label>快速填入厂商设置<select value="" disabled={managed} onChange={(event) => {
          const preset = providerPresets[form.serviceType].find((item) => item.id === event.target.value);
          if (preset) change(applyPreset(preset));
        }}>
          <option value="">选择后填入服务地址和模型标识</option>
          {providerPresets[form.serviceType].map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
        </select></label>
        <label>厂商名称<input value={form.provider} disabled={managed} onChange={(e) => change({ provider: e.target.value })} required maxLength={120} placeholder="选择预设或输入自定义厂商" /></label>
        <label>服务地址（Base URL）<input value={form.baseUrl} disabled={managed} onChange={(e) => change({ baseUrl: e.target.value })} type="url" maxLength={2048} placeholder="选填，例如 https://api.example.com/v1" /></label>
        {managed && <p className="config-default-hint">此模型由 BeefAPI 官方连接维护；密钥仅在服务端保存。</p>}
        <label>API 密钥<input type="password" value={form.apiKey} disabled={form.clearApiKey || managed} onChange={(e) => change({ apiKey: e.target.value })} autoComplete="new-password" placeholder={existing?.hasApiKey ? '已保存密钥；留空保留原密钥' : '选填，输入后保存到服务端'} /></label>
        {existing?.hasApiKey && !managed && <label className="form-check"><input type="checkbox" checked={form.clearApiKey} onChange={(e) => change({ clearApiKey: e.target.checked, apiKey: '' })} />清除已保存的密钥</label>}
        <label>Secret Key（第二密钥）<input type="password" value={form.secretKey} disabled={form.clearSecretKey || managed} onChange={event => change({ secretKey: event.target.value })} autoComplete="new-password" placeholder={existing?.hasSecretKey ? '已保存第二密钥；留空保留' : '按服务商要求填写，选填'}/></label>
        <p className="config-default-hint">模型列表使用 API 密钥与请求头；第二密钥当前仅安全保存。</p>
        {existing?.hasSecretKey && !managed && <label className="form-check"><input type="checkbox" checked={form.clearSecretKey} onChange={event => change({ clearSecretKey: event.target.checked, secretKey: '' })}/>清除已保存的第二密钥</label>}
        <details className="config-advanced"><summary>自定义请求头{form.headers.length ? ` · ${form.headers.length} 项` : ''}</summary><div className="config-advanced-body">
          <p className="config-default-hint">已保存的值不会回显；同名留空保留原值，删除条目后保存会清除。认证和传输字段由服务端管理。</p>
          {form.headers.map((header, index) => <div className="config-header-row" key={index}>
            <label>请求头 {index + 1} 名称<input value={header.name} onChange={event => change({ headersChanged: true, headers: form.headers.map((item, at) => at === index ? { ...item, name: event.target.value, hasValue: false } : item) })} autoComplete="off"/></label>
            <label>请求头 {index + 1} 值<input type="password" value={header.value} placeholder={header.hasValue ? '已保存；留空保留' : '请求头值'} autoComplete="new-password" onChange={event => change({ headersChanged: true, headers: form.headers.map((item, at) => at === index ? { ...item, value: event.target.value } : item) })}/></label>
            <button type="button" aria-label={`删除请求头 ${index + 1}`} onClick={() => change({ headersChanged: true, headers: form.headers.filter((_, at) => at !== index) })}>删除</button>
          </div>)}
          <button type="button" disabled={form.headers.length >= 32} onClick={() => change({ headersChanged: true, headers: [...form.headers, { name: '', value: '', hasValue: false }] })}>添加请求头</button>
        </div></details>
        <ModelIdentifierField form={form} existing={existing} disabled={pending || managed} onChange={(modelKey) => change({ modelKey })} />
        <RuntimeConfigFields value={form.runtime} serviceType={serviceType} readOnly={managed} onChange={runtime => change({ runtime })}/>
        <div className="config-form-toggles">
          <label className="form-check"><input type="checkbox" checked={form.enabled} onChange={(e) => {
            change({ enabled: e.target.checked });
            setMakeDefault(e.target.checked && !!existing?.isDefault);
          }} />启用此配置</label>
          <label className="form-check"><input type="checkbox" checked={form.enabled && makeDefault}
            disabled={!form.enabled || existing?.isDefault} onChange={e => setMakeDefault(e.target.checked)} />设为默认配置</label>
        </div>
        {existing?.isDefault && form.enabled ? <p className="config-default-hint">当前为默认配置；可在其他配置中设置新的默认配置。</p> : null}
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
