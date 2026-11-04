import { serviceLabels } from '@host/features/ai-config/editor/config-model';
import { Icon } from '@host/shared/ui/Icon';
import type { AiConfig, ServiceType } from '@host/types/ai-config/editor/config-model';
import { Button, Dropdown, Switch, Tooltip } from 'antd';
import { FlaskConical, Pencil } from 'lucide-react';
import './config-table.css';
export function ConfigTable({
  items,
  serviceType,
  busyId,
  onEdit,
  onDelete,
  onDefault,
  onTest,
  onEnabled,
}: {
  items: AiConfig[];
  serviceType: ServiceType;
  busyId: string | null;
  onEdit: (item: AiConfig) => void;
  onDelete: (item: AiConfig) => void;
  onDefault: (item: AiConfig) => void;
  onTest: (item: AiConfig) => void;
  onEnabled: (item: AiConfig) => void;
}) {
  if (!items.length)
    return (
      <div className="studio-empty">
        <h2>还没有{serviceLabels[serviceType]}</h2>
        <p>添加一个模型配置，就能在创作时使用。</p>
      </div>
    );
  return (
    <div className="config-table-wrap">
      <table className="config-table">
        <caption className="sr-only">{serviceLabels[serviceType]}配置</caption>
        <thead>
          <tr>
            <th scope="col">模型名称</th>
            <th scope="col">模型</th>
            <th scope="col">服务地址</th>
            <th scope="col">状态</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} aria-busy={busyId === item.id}>
              <td data-label="模型名称">
                <div className="config-identity">
                  <strong>{item.name}</strong>
                  {item.isDefault && <span className="status-badge">默认模型</span>}
                </div>
                <span className="config-secondary">{item.provider}</span>
              </td>
              <td data-label="模型">
                <span className="config-model-key">{item.modelKey}</span>
              </td>
              <td data-label="服务地址">
                <span className="config-secondary config-url" title={item.baseUrl}>
                  {item.baseUrl || '未设置地址'}
                </span>
                <span className="config-secondary">{item.hasApiKey ? '密钥已配置' : '未配置密钥'}</span>
              </td>
              <td data-label="状态">
                <Switch size="small" aria-label={`启用模型：${item.name}`} checked={item.enabled}
                  disabled={!!busyId} loading={busyId === item.id} onChange={() => onEnabled(item)} />
              </td>
              <td data-label="操作">
                <div className="config-actions">
                  <Tooltip title="编辑"><Button type="text" aria-label={`编辑模型：${item.name}`} disabled={!!busyId}
                    onClick={() => onEdit(item)} icon={<Pencil size={16} />} /></Tooltip>
                  <Tooltip title="测试"><Button type="text" aria-label={`测试模型：${item.name}`} disabled={!!busyId || !item.enabled}
                    onClick={() => onTest(item)} icon={<FlaskConical size={16} />} /></Tooltip>
                  <Dropdown
                    trigger={['click']}
                    menu={{
                      items: [
                        {
                          key: 'default',
                          label: item.isDefault ? '已是默认模型' : '设为默认',
                          disabled: item.isDefault || !item.enabled,
                        },
                        { key: 'delete', label: '删除配置', danger: true },
                      ],
                      onClick: ({ key }) =>
                        key === 'default' ? onDefault(item) : onDelete(item),
                    }}
                  >
                    <Button
                      type="text"
                      disabled={!!busyId}
                      aria-label={`更多操作：${item.name}`}
                      icon={<Icon name="more" size={18} />}
                    />
                  </Dropdown>
                </div>
                {busyId === item.id && <span role="status">处理中…</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
