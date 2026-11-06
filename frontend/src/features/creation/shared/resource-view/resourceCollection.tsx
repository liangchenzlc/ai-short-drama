import { ResourceViewToggle } from '@host/features/creation/shared/resource-view/ResourceViewToggle';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import { Checkbox } from 'antd';
import { Image as ImageIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import './resourceCollection.css';

export type ResourceCollectionItem = {
  id: string;
  label: string;
  selectionLabel: string;
  description: string;
  image: string | null;
  status: string;
};

export function ResourceCollection({ title, items, mode, selectedIds, filters, toolbar, children, onMode,
  onToggle, onOpen }: {
  title: string;
  items: ResourceCollectionItem[];
  mode: ResourceView;
  selectedIds: string[];
  filters?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
  onMode: (mode: ResourceView) => void;
  onToggle: (id: string, checked: boolean) => void;
  onOpen: (id: string) => void;
}) {
  return (
    <section className="resourceCollection" aria-label={title}>
      <header className="resourceCollectionHeader">
        <div className="resourceCollectionFilters">{filters}</div>
        <span className="resourceSelectionCount">已选 {selectedIds.length} 项</span>
        <ResourceViewToggle value={mode} onChange={onMode} options={['single', 'grid', 'list']} />
      </header>
      {toolbar}
      {mode === 'single' ? <div className="resourceCollectionSingle">{children}</div> :
        <div className={'resourceCollectionItems is' + (mode === 'grid' ? 'Grid' : 'List')}>
          {items.map((item) => <article className="resourceCollectionItem" key={item.id}>
            <Checkbox aria-label={item.selectionLabel} checked={selectedIds.includes(item.id)}
              onChange={(event) => onToggle(item.id, event.target.checked)} />
            <button type="button" className="resourceItemBody" aria-label={'打开' + item.label} onClick={() => onOpen(item.id)}>
              <span className="resourceItemImage">{item.image ? <img src={item.image} alt="" /> : <ImageIcon size={24} />}</span>
              <span className="resourceItemText"><strong>{item.label}</strong>
                <span className="resourceItemDescription">{item.description}</span>
                <span className="resourceItemStatus">{item.status}</span>
              </span>
            </button>
          </article>)}
          {!items.length && <div className="resourceCollectionEmpty">没有匹配的内容</div>}
        </div>}
    </section>
  );
}