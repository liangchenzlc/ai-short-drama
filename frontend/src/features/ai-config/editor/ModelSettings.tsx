import { ConfigForm } from '@host/features/ai-config/editor/ConfigForm';
import { ConfigTable } from '@host/features/ai-config/editor/ConfigTable';
import { serviceLabels } from '@host/features/ai-config/editor/config-model';
import { useModelSettings } from '@host/features/ai-config/editor/useModelSettings';
import { ModelConfigTest } from '@host/features/ai-config/testing/ModelConfigTest';
import { Dialog } from '@host/shared/ui/Dialog';
import { Icon } from '@host/shared/ui/Icon';
import type { ServiceType } from '@host/types/ai-config/editor/config-model';
import { Button, Pagination, Skeleton } from 'antd';

export function ModelSettings({ serviceType }: { serviceType: ServiceType }) {
  const models = useModelSettings(serviceType);
  return <div className="model-settings">
    <div className="model-settings-toolbar">
      <span className="muted">{models.total} 个模型</span>
      <Button type="primary" icon={<Icon name="plus" size={16} />} onClick={models.add} disabled={!!models.busyId}>
        新增{serviceLabels[serviceType]}
      </Button>
    </div>
    {models.loading ? <div className="studio-empty" role="status" aria-label="正在加载模型"><Skeleton active paragraph={{ rows: 3 }} /></div>
      : models.error ? <div className="studio-empty" role="alert"><p>{models.error}</p><Button onClick={models.reload}>重新加载</Button></div>
        : <><ConfigTable items={models.items} serviceType={serviceType} busyId={models.busyId}
          onEdit={models.edit} onDelete={models.confirmDelete} onDefault={models.setDefault}
          onTest={models.setTesting} onEnabled={models.toggleEnabled} />
          {models.total > models.pageSize && <Pagination className="model-settings-pagination" current={models.offset / models.pageSize + 1}
            pageSize={models.pageSize} total={models.total} showSizeChanger={false}
            disabled={!!models.busyId} onChange={page => models.setOffset((page - 1) * models.pageSize)} />}</>}
    {models.formOpen && <ConfigForm key={models.formRevision} existing={models.editing} serviceType={serviceType}
      onSave={models.save} onClose={models.closeForm} onReload={models.reloadEditing} />}
    {models.deleting && <Dialog title="删除模型" onClose={() => models.setDeleting(null)} canClose={!models.busyId}>
      <p>确定删除「{models.deleting.name}」？{models.deleting.isDefault && '删除后需要重新选择默认模型。'}</p>
      {models.deleteError && <p role="alert" className="form-error">{models.deleteError}</p>}
      <div className="dialog-actions"><Button onClick={() => models.setDeleting(null)} disabled={!!models.busyId}>取消</Button>
        {models.deleteConflict ? <Button onClick={() => { models.setDeleting(null); models.reload(); }}>重新加载列表</Button>
          : <Button danger type="primary" loading={!!models.busyId} onClick={() => void models.remove()}>删除</Button>}
      </div>
    </Dialog>}
    {models.testing && <ModelConfigTest config={models.testing} onClose={() => models.setTesting(null)} />}
  </div>;
}
