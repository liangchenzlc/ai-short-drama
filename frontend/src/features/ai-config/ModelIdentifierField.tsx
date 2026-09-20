import { useEffect, useRef, useState, type ComponentRef } from 'react';
import { AutoComplete, Input } from 'antd';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { errorMessage, isCancelled } from '../../api/http';
import type { AiConfig, ConfigDraft } from './config-model';

export function ModelIdentifierField({ form, existing, disabled, onChange }: {
  form: ConfigDraft;
  existing: AiConfig | null;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const [models, setModels] = useState<{ value: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const input = useRef<ComponentRef<typeof AutoComplete>>(null);
  const options = models.filter((model) => model.value.toLowerCase().includes(search.toLowerCase()));
  const popupVisible = open && options.length > 0 && !disabled && !loading;
  const active = useRef<AbortController | null>(null);
  // Used only in memory to discard replies for credentials/addresses that changed mid-flight.
  const signature = JSON.stringify([form.baseUrl, form.apiKey, form.clearApiKey, existing?.id]);
  const latest = useRef(signature);
  latest.current = signature;
  useEffect(() => {
    active.current?.abort();
    setModels([]); setLoading(false); setFeedback(''); setFailed(false); setOpen(false); setSearch('');
    return () => active.current?.abort();
  }, [signature]);

  async function discover() {
    if (disabled || loading) return;
    if (!form.baseUrl.trim()) { setFailed(true); setFeedback('请先填写服务地址，再获取模型列表。'); return; }
    const controller = new AbortController();
    active.current?.abort(); active.current = controller;
    const requestedSignature = signature;
    setLoading(true); setFailed(false); setFeedback(''); setModels([]); setOpen(false); setSearch('');
    try {
      const result = await aiModelConfigs.discoverModels({
        base_url: form.baseUrl.trim(),
        ...(existing ? { config_id: existing.id } : {}),
        ...(form.clearApiKey ? { apikey: null } : form.apiKey ? { apikey: form.apiKey } : {}),
      }, controller.signal);
      if (controller.signal.aborted || latest.current !== requestedSignature) return;
      setModels(result.items.map((model) => ({ value: model.id })));
      setSearch('');
      setOpen(result.items.length > 0);
      if (result.items.length) input.current?.focus();
      setFeedback(result.items.length
        ? `已获取 ${result.items.length} 个模型，可直接下拉选择，也可输入搜索。${result.truncated ? '当前列表为部分结果，仍可手动填写其他模型。' : ''}`
        : '服务未返回可用模型，可以手动填写模型标识。');
    } catch (cause) {
      if (controller.signal.aborted || latest.current !== requestedSignature || isCancelled(cause)) return;
      setFailed(true); setFeedback(errorMessage(cause));
    } finally {
      if (active.current === controller && latest.current === requestedSignature) setLoading(false);
    }
  }

  return <div className="model-discovery-field">
    <label htmlFor="ai-model-key">模型标识</label>
    <div className="model-discovery-input">
      <AutoComplete ref={input} id="ai-model-key" value={form.modelKey} options={options} onChange={(value) => onChange(value.slice(0, 255))} disabled={disabled}
        open={popupVisible} onOpenChange={setOpen}
        onFocus={() => { setSearch(''); setOpen(true); }}
        onSearch={(value) => { setSearch(value.slice(0, 255)); setOpen(true); }}
        onSelect={() => { setSearch(''); setOpen(false); }}
        onInputKeyDown={(event) => {
          if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); }
        }}
        getPopupContainer={(trigger) => trigger.parentElement!}
        filterOption={false}
        popupMatchSelectWidth={true}>
        <Input required autoComplete="off"
          suffix={<button type="button" className="model-dropdown-toggle" aria-label={popupVisible ? '收起模型列表' : '展开模型列表'}
            aria-expanded={popupVisible} disabled={disabled || loading || !models.length}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => { input.current?.focus(); setSearch(''); setOpen(!popupVisible); }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
          </button>}
          aria-describedby="model-discovery-feedback" placeholder="下拉选择或输入模型标识" />
      </AutoComplete>
      <button type="button" onClick={discover} disabled={disabled || loading} aria-busy={loading}>
        {loading ? '获取中…' : '获取模型'}
      </button>
    </div>
    <p id="model-discovery-feedback" className={failed ? 'form-error' : 'model-discovery-hint'} role={failed ? 'alert' : 'status'}>
      {search && models.length > 0 && !options.length && !failed
        ? '没有匹配的模型，可以保留当前输入作为自定义模型标识。'
        : feedback || '使用当前服务地址和密钥获取列表；不支持探测的服务可手动填写。'}
    </p>
  </div>;
}
