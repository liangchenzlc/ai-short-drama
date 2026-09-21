import { Button, Dropdown } from 'antd';
import { Icon } from '../../components/ui/Icon';
import { serviceLabels, type AiConfig, type ServiceType } from './config-model';

export function ConfigTable({ items, serviceType, busyId, onEdit, onDelete, onDefault }: {
  items: AiConfig[]; serviceType: ServiceType; busyId: string | null;
  onEdit: (item: AiConfig) => void; onDelete: (item: AiConfig) => void; onDefault: (item: AiConfig) => void;
}) {
  if (!items.length) return <div className="studio-empty"><h2>还没有{serviceLabels[serviceType]}</h2><p>添加一个模型配置，就能在创作时使用。</p></div>;
  return <div className="config-table-wrap"><table className="config-table">
    <caption className="sr-only">{serviceLabels[serviceType]}配置</caption>
    <thead><tr><th scope="col">模型名称</th><th scope="col">模型与服务地址</th><th scope="col">状态</th><th scope="col">密钥</th><th scope="col">操作</th></tr></thead>
    <tbody>{items.map(item => <tr key={item.id} aria-busy={busyId === item.id}>
      <td data-label="模型名称"><div className="config-identity"><strong>{item.name}</strong>{item.isDefault && <span className="status-badge">默认模型</span>}</div><span className="config-secondary">{item.provider}</span></td>
      <td data-label="模型与地址"><span className="config-model-key">{item.modelKey}</span><span className="config-secondary config-url" title={item.baseUrl}>{item.baseUrl || '未设置地址'}</span></td>
      <td data-label="状态"><span className={`status-badge ${item.enabled ? 'is-success' : 'is-neutral'}`}>{item.enabled ? '已启用' : '已停用'}</span></td>
      <td data-label="密钥">{item.hasApiKey ? '已配置' : '未配置'}</td>
      <td data-label="操作"><div className="config-actions"><Button disabled={!!busyId} onClick={() => onEdit(item)}>编辑</Button><Dropdown trigger={['click']} menu={{ items: [
        { key: 'default', label: item.isDefault ? '已是默认模型' : '设为默认', disabled: item.isDefault || !item.enabled },
        { key: 'delete', label: '删除配置', danger: true },
      ], onClick: ({ key }) => key === 'default' ? onDefault(item) : onDelete(item) }}><Button disabled={!!busyId} aria-label={`更多操作：${item.name}`} icon={<Icon name="more" size={18}/>} /></Dropdown></div>{busyId === item.id && <span role="status">处理中…</span>}</td>
    </tr>)}</tbody>
  </table></div>;
}
