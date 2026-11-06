import { ProjectAssetCard } from '@host/features/creation/assets/list/ProjectAssetCard';
import '@host/features/creation/assets/list/index.css';
import { ResourceViewToggle } from '@host/features/creation/shared/resource-view/ResourceViewToggle';
import type { AssetKind, LibraryAssetRead } from '@host/types/creation/assets/management';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import type { AssetView } from '@host/types/creation/workspace/workbenchView';
import { Button, Input } from 'antd';
import { History, Plus, Search } from 'lucide-react';

export function WorkbenchPreviewAssetRail({ assets, visible, selectedId, query, kind, view, onQuery, onKind,
  onView, onOpen, onCreate, onDelete, onHistory }: {
  assets: AssetView[];
  visible: AssetView[];
  selectedId: string;
  query: string;
  kind: AssetKind | 'all';
  view: ResourceView;
  onQuery: (value: string) => void;
  onKind: (kind: AssetKind | 'all') => void;
  onView: (view: ResourceView) => void;
  onOpen: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  onHistory: () => void;
}) {
  const categories = { all: '全部', character: '角色', scene: '场景', prop: '道具' } as const;
  return (
    <section className={'standard-assets-list resource-view-' + view} aria-label="素材">
      <header className="standard-assets-heading">
        <h2><span className="standard-assets-title-text">资产库</span><small>{assets.length}</small></h2>
        <div className="standard-assets-tools">
          <ResourceViewToggle value={view} onChange={onView} options={['list', 'grid']} />
          <Button type="text" aria-label="资产提取记录" title="资产提取记录" icon={<History size={16} />} onClick={onHistory} />
        </div>
      </header>
      <div className="standard-assets-filters">
        <Input prefix={<Search size={15} aria-hidden="true" />} value={query} onChange={(event) => onQuery(event.target.value)}
          aria-label="搜索素材" placeholder="搜索名称或描述" allowClear />
        <div className="standard-asset-category-tabs" aria-label="素材类别">
          {(Object.entries(categories) as [AssetKind | 'all', string][]).map(([value, label]) =>
            <button type="button" key={value} aria-label={label} aria-pressed={kind === value} onClick={() => onKind(value)}>
              <span>{label}</span><small>{value === 'all' ? assets.length : assets.filter((asset) => asset.kind === value).length}</small>
            </button>)}
        </div>
      </div>
      <div className="standard-assets-scroll">
        {visible.map((asset, index) => <ProjectAssetCard key={asset.id} asset={asLibraryAsset(asset, index)}
          selected={selectedId === asset.id} disabled={false} readOnly={false} removing={false} entryRef={() => {}}
          onOpen={() => onOpen(asset.id)} onRemove={() => onDelete(asset.id)} />)}
        {!visible.length && <div className="standard-assets-list-state"><strong>{assets.length ? '没有匹配的条目' : '还没有资产'}</strong></div>}
      </div>
      <Button className="resource-create-button" type="text" aria-label="创建资产" icon={<Plus size={16} />} onClick={onCreate}>创建</Button>
    </section>
  );
}

function asLibraryAsset(asset: AssetView, position: number): LibraryAssetRead {
  const image = asset.adoptedImage;
  return {
    id: asset.id, kind: asset.kind, name: asset.name, label: '', description: asset.description, prompt: asset.imagePrompt,
    tags: asset.tags, scene_time: '', state: 'confirmed', row_version: '1', media_id: image?.media_id ?? null,
    image: image?.url ? { media_id: image.media_id, url: image.url, width: image.width, height: image.height } : null,
    reference_count: 0, created_at: null, updated_at: null, link_id: null, position,
  };
}