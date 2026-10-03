import { useEffect } from 'react';
import { Alert, Button, Select } from 'antd';
import type { GenerationKind } from '../../api/types/generations';
import { useConfigCatalog } from '../ai-config/ConfigCatalogProvider';
import { resolveConfigSelection } from '../ai-config/config-selection';
import { useModelPreferences } from '../auth/ModelPreferences';

export function ConfigSelect({ kind, value, onChange, onResolvedChange, allowDefault = true, autoDefault = true, disabled = false, label = '模型配置', preferenceKey }: {
  kind: GenerationKind; value?: string; onChange?: (value: string | undefined) => void; onResolvedChange?: (value: string | undefined) => void; allowDefault?: boolean; autoDefault?: boolean; disabled?: boolean; label?: string; preferenceKey?: string;
}) {
  const preferences = useModelPreferences();
  const remember = preferences.enabled && allowDefault && autoDefault;
  const key = preferenceKey ?? `model:${kind}:${label}`;
  const waiting = remember && !preferences.ready;
  const { items, loading, error, refresh } = useConfigCatalog(kind);
  const selected = resolveConfigSelection(items, kind, value ?? (remember ? preferences.values[key] : undefined), allowDefault && autoDefault);
  const usableSelected = selected && items.some((item) => item.id === selected && item.enabled && item.serviceType === kind) ? selected : undefined;
  useEffect(() => { if (!loading && !waiting) onResolvedChange?.(usableSelected); }, [loading, waiting, onResolvedChange, usableSelected]);
  return <div className="generation-config-select">
    <Select aria-label={label} value={selected} onChange={id => { onChange?.(id); if (remember) void preferences.save(key, id); }} loading={loading || waiting} disabled={disabled || waiting} allowClear showSearch optionFilterProp="label"
      placeholder={!allowDefault ? '全部模型配置' : autoDefault ? '请选择模型配置' : '沿用原任务配置'}
      options={items.map((item) => ({ value: item.id, label: `${item.name} · ${item.modelKey}${item.isDefault ? '（默认）' : ''}${!item.enabled ? '（停用）' : ''}`, disabled: allowDefault && !item.enabled }))}
      notFoundContent={loading ? '加载中…' : '暂无配置，请先前往 AI 配置添加'} />
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={refresh}>重试</Button>} />}
    {remember && preferences.error && <Alert type="warning" message={`模型偏好未同步：${preferences.error}`} />}
    {allowDefault && autoDefault && !loading && !error && !selected && <Alert type="warning" showIcon message="尚未设置此类型的默认模型，请先选择一个已启用的模型。" />}
  </div>;
}
