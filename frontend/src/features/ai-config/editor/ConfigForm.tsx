import { aiModelConfigs } from '@host/api/ai-config/management';
import { ApiError, errorMessage } from '@host/api/http';
import { CredentialField } from '@host/features/ai-config/editor/CredentialField';
import { createConfigDraft } from '@host/features/ai-config/editor/config-form-model';
import { serviceLabels } from '@host/features/ai-config/editor/config-model';
import { ModelIdentifierField } from '@host/features/ai-config/editor/ModelIdentifierField';
import { confirmAction } from '@host/shared/ui/confirm';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { ModelConfigurationOptionsDto } from '@host/types/ai-config/api';
import type { AiConfig, ConfigDraft, ServiceType } from '@host/types/ai-config/editor/config-model';
import { Button, Switch, Tooltip } from 'antd';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import './config-form.css';

export function ConfigForm({ existing, serviceType, onClose, onSave, onReload }: {
  existing: AiConfig | null; serviceType: ServiceType; onClose: () => void;
  onSave: (draft: ConfigDraft, makeDefault: boolean, createKey: string) => Promise<void>;
  onReload: () => Promise<void>;
}) {
  const [form, setForm] = useState(() => createConfigDraft(existing, serviceType));
  const [makeDefault, setMakeDefault] = useState(existing?.isDefault ?? false);
  const [options, setOptions] = useState<ModelConfigurationOptionsDto | null>(null);
  const [optionsError, setOptionsError] = useState('');
  const [optionsRevision, setOptionsRevision] = useState(0);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [pending, setPending] = useState(false);
  const initial = useRef(JSON.stringify(form));
  const createKey = useRef(crypto.randomUUID());
  const dirty = JSON.stringify(form) !== initial.current || makeDefault !== (existing?.isDefault ?? false);
  const selectedInterface = options?.interfaces.find(item => item.id === form.runtime.protocol);
  const dualKey = selectedInterface?.requires_secret_key ?? false;
  useEffect(() => {
    const controller = new AbortController();
    setOptionsError('');
    aiModelConfigs.options(serviceType, controller.signal).then(value => { if (!controller.signal.aborted) setOptions(value); }).catch(cause => { if (!controller.signal.aborted) setOptionsError(errorMessage(cause)); });
    return () => controller.abort();
  }, [serviceType, optionsRevision]);
  useEffect(() => {
    if (!dirty && !pending && !uncertain) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty, pending, uncertain]);
  const change = (partial: Partial<ConfigDraft>) => setForm(old => ({ ...old, ...partial }));
  async function close() {
    if (pending || uncertain) return;
    if (dirty && !await confirmAction('关闭会放弃尚未保存的模型修改，确定关闭？')) return;
    onClose();
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true); setError('');
    try { await onSave(form, form.enabled && makeDefault, createKey.current); }
    catch (cause) {
      setError(errorMessage(cause));
      setConflict(cause instanceof ApiError && cause.status === 409);
      setUncertain(!existing && !(cause instanceof ApiError && cause.code === 'DEFAULT_UPDATE_FAILED')
        && (!(cause instanceof ApiError) || !cause.status || cause.status >= 500));
    } finally { setPending(false); }
  }
  return <Dialog title={`${existing ? '编辑' : '新增'}${serviceLabels[serviceType]}`} className="model-config-dialog" onClose={() => void close()} canClose={!pending && !uncertain}>
    <form className="studio-form ai-config-form" onSubmit={submit}>
      <fieldset disabled={pending || uncertain || !options}>
        <div className="config-field-pair">
          <label>服务商<select aria-label="服务商" value={form.provider} required onChange={event => {
            const preset = options!.providers.find(item => item.id === event.target.value);
            change({ provider: event.target.value, ...(preset ? { baseUrl: preset.base_url, runtime: { protocol: preset.interface_type, apiFormat: options!.interfaces.find(item => item.id === preset.interface_type)!.api_format } } : {}) });
          }}><option value="" disabled>选择服务商</option>{options?.providers.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}
            {form.provider && !options?.providers.some(item => item.id === form.provider) && form.provider !== 'custom' && <option value={form.provider}>{form.provider}</option>}
            <option value="custom">自定义</option></select></label>
          <label>接口类型<select aria-label="接口类型" value={form.runtime.protocol} onChange={event => {
            const selected = options!.interfaces.find(item => item.id === event.target.value)!;
            change({ runtime: { protocol: selected.id, apiFormat: selected.api_format } });
          }}>{options?.interfaces.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}
            {options && existing && !options.interfaces.some(item => item.id === form.runtime.protocol) &&
              <option value={form.runtime.protocol}>{form.runtime.protocol}</option>}
          </select></label>
        </div>
        <label>服务地址<input aria-label="服务地址" type="url" value={form.baseUrl} required maxLength={2048} placeholder="https://" onChange={event => change({ baseUrl: event.target.value })} /></label>
        <div className={dualKey ? 'config-field-pair' : ''}>
          <CredentialField label={dualKey ? 'Access Key' : 'API 密钥'} value={form.apiKey} stored={existing?.hasApiKey ?? false} cleared={form.clearApiKey} disabled={pending || uncertain}
            onChange={(apiKey, clearApiKey) => change({ apiKey, clearApiKey })} />
          {dualKey && <CredentialField label="Secret Key" value={form.secretKey} stored={existing?.hasSecretKey ?? false} cleared={form.clearSecretKey} disabled={pending || uncertain}
            onChange={(secretKey, clearSecretKey) => change({ secretKey, clearSecretKey })} />}
        </div>
        <ModelIdentifierField form={form} existing={existing} disabled={pending || uncertain} onChange={modelKey => change({ modelKey })} />
        <label><span>显示名称 <span className="muted">（选填）</span></span><input aria-label="显示名称" value={form.name} maxLength={120} placeholder="默认使用模型标识" onChange={event => change({ name: event.target.value })} /></label>
        <div className="config-form-toggles"><label>启用模型<Switch aria-label="启用模型" checked={form.enabled} onChange={enabled => change({ enabled })} /></label>
          <label>设为默认<Tooltip title={existing?.isDefault ? '当前默认模型，选择其他模型可更改' : undefined}>
            <span><Switch aria-label="设为默认" checked={makeDefault} disabled={!form.enabled || existing?.isDefault} onChange={setMakeDefault} /></span>
          </Tooltip></label></div>
      </fieldset>
      {optionsError && <StudioFeedback type="error" message={optionsError} noticeKey="model-options" actions={<Button onClick={() => setOptionsRevision(value => value + 1)}>重试</Button>} />}
      {error && <StudioFeedback persistent type="error" message={error} noticeKey="model-save" />}
      <footer className="dialog-actions"><Button disabled={pending || uncertain} onClick={() => void close()}>取消</Button>
        {conflict ? <Button loading={pending} onClick={() => void onReload()}>读取最新配置</Button> : <Button type="primary" htmlType="submit" aria-label={uncertain ? '核对保存结果' : '保存'} loading={pending} disabled={!options}>{uncertain ? '核对保存结果' : '保存'}</Button>}
      </footer>
    </form>
  </Dialog>;
}
