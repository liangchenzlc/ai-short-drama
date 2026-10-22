import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Alert, Button, Select } from 'antd';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import type { GenerationKind } from '../../api/types/generations';
import { useConfigCatalog } from '../ai-config/ConfigCatalogProvider';
import { resolveConfigSelection } from '../ai-config/config-selection';
import { useModelPreferences } from '../auth/ModelPreferences';

export function ConfigSelect({ kind, value, onChange, onResolvedChange, allowDefault = true, autoDefault = true, disabled = false, label = '模型配置', preferenceKey, optionLayout, presentation, feedbackContainer }: {
  kind: GenerationKind; value?: string; onChange?: (value: string | undefined) => void; onResolvedChange?: (value: string | undefined) => void; allowDefault?: boolean; autoDefault?: boolean; disabled?: boolean; label?: string; preferenceKey?: string;
  optionLayout?: 'single' | 'detailed' | 'compact'; presentation?: 'standard-creation'; feedbackContainer?: HTMLElement | null;
}) {
  const preferences = useModelPreferences();
  const remember = preferences.enabled && allowDefault && autoDefault;
  const key = preferenceKey ?? `model:${kind}:${label}`;
  const waiting = remember && !preferences.ready;
  const { items, loading, error, refresh } = useConfigCatalog(kind);
  const selected = resolveConfigSelection(items, kind, value ?? (remember ? preferences.values[key] : undefined), allowDefault && autoDefault);
  const usableSelected = selected && items.some((item) => item.id === selected && item.enabled && item.serviceType === kind) ? selected : undefined;
  useEffect(() => { if (!loading && !waiting) onResolvedChange?.(usableSelected); }, [loading, waiting, onResolvedChange, usableSelected]);
  const compact = optionLayout === 'compact';
  const detailed = compact || optionLayout === 'detailed' || (optionLayout === undefined && presentation === 'standard-creation');
  const hasFeedback = error || remember && preferences.error || allowDefault && autoDefault && !loading && !selected;
  const feedback = presentation ? <>
    {error && <StudioFeedback type="error" message={error} action={<Button size="small" onClick={refresh}>重试模型列表</Button>}/>}
    {remember && preferences.error && <StudioFeedback type="warning" message={`模型偏好未同步：${preferences.error}`}/>}
  </> : hasFeedback ? <div className="generation-config-feedback">
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={refresh}>重试</Button>} />}
    {remember && preferences.error && <Alert type="warning" message={`模型偏好未同步：${preferences.error}`} />}
    {allowDefault && autoDefault && !loading && !error && !selected && <Alert type="warning" showIcon message={presentation ? '请先选择一个已启用的模型。' : '尚未设置此类型的默认模型，请先选择一个已启用的模型。'} />}
  </div> : null;
  return <div className="generation-config-select">
    <Select aria-label={label} value={selected} onChange={id => { onChange?.(id); if (remember) void preferences.save(key, id); }} loading={loading || waiting} disabled={disabled || waiting} allowClear={!compact} showSearch optionFilterProp="label"
      status={presentation && error ? 'error' : undefined}
      suffixIcon={compact && !loading && !waiting ? null : undefined}
      labelRender={compact ? option => items.find(item => item.id === option.value)?.name ?? option.label : undefined}
      placeholder={!allowDefault ? '全部模型配置' : autoDefault ? '请选择模型配置' : '沿用原任务配置'}
      options={items.map((item) => ({ value: item.id, label: `${item.name} · ${item.modelKey}${item.isDefault ? '（默认）' : ''}${!item.enabled ? '（停用）' : ''}`, disabled: allowDefault && !item.enabled, name: item.name, modelKey: item.modelKey, isDefault: item.isDefault, enabled: item.enabled }))}
      optionRender={detailed ? option => <div className="creation-model-option"><strong title={option.data.name}>{option.data.name}</strong><span title={option.data.modelKey}>{option.data.modelKey}{option.data.isDefault && ' · 默认'}{!option.data.enabled && ' · 停用'}</span></div> : undefined}
      popupMatchSelectWidth={detailed ? false : undefined}
      classNames={presentation ? { popup: { root: `standard-creation-popup creation-model-popup${compact ? ' creation-model-popup-compact' : ''}` } } : undefined}
      styles={detailed ? { popup: { root: { width: `min(${compact ? 240 : 360}px, calc(100vw - 24px))`, minWidth: compact ? 0 : 'min(280px, calc(100vw - 24px))', maxWidth: 'calc(100vw - 24px)' } } } : undefined}
      listItemHeight={compact ? 44 : detailed ? 48 : undefined} listHeight={compact ? 220 : detailed ? 240 : undefined}
      notFoundContent={loading ? '加载中…' : '暂无配置，请先前往 AI 配置添加'} />
    {feedbackContainer ? createPortal(feedback, feedbackContainer) : feedback}
  </div>;
}
