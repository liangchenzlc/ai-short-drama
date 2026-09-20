import { useEffect, useState } from 'react';
import { Alert, Button, Select } from 'antd';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import type { AiConfig } from '../ai-config/config-model';
import type { GenerationKind } from '../../api/types/generations';
import { generationError } from './presentation';

export function ConfigSelect({ kind, value, onChange, allowDefault = true, disabled = false }: {
  kind: GenerationKind; value?: string; onChange?: (value: string | undefined) => void; allowDefault?: boolean; disabled?: boolean;
}) {
  const [items, setItems] = useState<AiConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setItems([]);
    async function load() {
      const result: AiConfig[] = [];
      try {
        let offset = 0;
        do {
          const page = await aiModelConfigs.list(kind, offset, 100, controller.signal);
          if (controller.signal.aborted) return;
          result.push(...page.items); offset += page.items.length;
          if (!page.items.length || offset >= page.total) break;
        } while (!controller.signal.aborted);
        setItems(result);
      } catch (cause) { if (!controller.signal.aborted) setError(generationError(cause)); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [kind, revision]);
  return <div className="generation-config-select">
    <Select aria-label="模型配置" value={value} onChange={onChange} loading={loading} disabled={disabled} allowClear showSearch optionFilterProp="label"
      placeholder={allowDefault ? '使用此类型的默认配置' : '全部模型配置'}
      options={items.map((item) => ({ value: item.id, label: `${item.name} · ${item.modelKey}${item.isDefault ? '（默认）' : ''}${!item.enabled ? '（停用）' : ''}`, disabled: allowDefault && !item.enabled }))}
      notFoundContent={loading ? '加载中…' : '暂无配置，请先前往 AI 配置添加'} />
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRevision((n) => n + 1)}>重试</Button>} />}
    {allowDefault && !loading && !error && !value && !items.some((item) => item.enabled && item.isDefault) && <Alert type="warning" showIcon message="尚未设置此类型的默认模型，请先选择一个已启用的模型。" />}
  </div>;
}
