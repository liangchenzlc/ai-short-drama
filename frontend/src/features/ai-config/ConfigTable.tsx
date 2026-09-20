import { serviceLabels, type AiConfig, type ServiceType } from './config-model';

export function ConfigTable({ items, serviceType, busyId, onEdit, onDelete, onDefault }: {
  items: AiConfig[];
  serviceType: ServiceType;
  busyId: string | null;
  onEdit: (item: AiConfig) => void;
  onDelete: (item: AiConfig) => void;
  onDefault: (item: AiConfig) => void;
}) {
  if (!items.length) return <div className="studio-empty"><h2>还没有{serviceLabels[serviceType]}</h2><p>点击上方添加按钮，填写提供商和模型信息。</p></div>;
  return <div className="config-table-wrap"><table className="config-table">
    <thead><tr><th>名称</th><th>提供商</th><th>服务地址</th><th>模型标识</th><th>状态</th><th>密钥</th><th>默认</th><th>操作</th></tr></thead>
    <tbody>{items.map((item) => <tr key={item.id} aria-busy={busyId === item.id}>
      <td className="config-name">{item.name}</td><td>{item.provider}</td>
      <td className="config-url" title={item.baseUrl}>{item.baseUrl || '—'}</td>
      <td className="config-model-key" title={item.modelKey}>{item.modelKey}</td>
      <td>{item.enabled ? '已启用' : '已停用'}</td><td>{item.hasApiKey ? '已配置' : '未配置'}</td><td>{item.isDefault ? '默认' : '—'}</td>
      <td><div className="config-actions">
        <button disabled={!!busyId} onClick={() => onEdit(item)}>编辑</button>
        <button disabled={!!busyId || item.isDefault || !item.enabled} title={!item.enabled ? '请先编辑并启用此配置' : undefined} onClick={() => onDefault(item)}>设为默认</button>
        <button disabled={!!busyId} onClick={() => onDelete(item)}>删除</button>
      </div>{busyId === item.id && <span role="status">处理中…</span>}</td>
    </tr>)}</tbody>
  </table></div>;
}
