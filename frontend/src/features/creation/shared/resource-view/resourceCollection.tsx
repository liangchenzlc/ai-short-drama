import { ResourceViewToggle } from '@host/features/creation/shared/resource-view/ResourceViewToggle';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import { Checkbox } from 'antd';
import { Image as ImageIcon } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import './resourceCollection.css';

export type ResourceCollectionItem = {
  id: string;
  label: string;
  selectionLabel: string;
  description: string;
  image: string | null;
  imageCandidate?: boolean;
  status: string | string[];
};

export function ResourceCollection({ title, items, mode, selectedIds, toolbar, children, selectable = true, onVisibleChange, onCandidateState, onMode,
  onToggle, onOpen }: {
  title: string;
  items: ResourceCollectionItem[];
  mode: ResourceView;
  selectedIds: string[];
  toolbar?: ReactNode;
  children: ReactNode;
  selectable?: boolean;
  onVisibleChange?: (ids: string[]) => void;
  onCandidateState?: (id: string, url: string, loaded: boolean) => void;
  onMode: (mode: ResourceView) => void;
  onToggle: (id: string, checked: boolean) => void;
  onOpen: (id: string) => void;
}) {
  const statusId = useId();
  const collectionRef = useRef<HTMLDivElement>(null);
  const itemIds = JSON.stringify(items.map((item) => item.id));
  useEffect(() => {
    if (!onVisibleChange) return;
    onVisibleChange([]);
    if (mode === 'single' || !collectionRef.current) return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const id = (entry.target as HTMLElement).dataset.resourceId!;
        if (entry.isIntersecting) visible.add(id);
        else visible.delete(id);
      }
      onVisibleChange([...visible]);
    }, { root: collectionRef.current });
    collectionRef.current.querySelectorAll('[data-resource-id]').forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, [itemIds, mode, onVisibleChange]);
  return (
    <section className="resourceCollection" aria-label={title}>
      <header className="resourceCollectionHeader">
        <div className="resourceCollectionFilters">{toolbar}</div>
        {selectable && <span className="resourceSelectionCount">已选 {selectedIds.length} 项</span>}
        <ResourceViewToggle value={mode} onChange={onMode} options={['single', 'grid', 'list']} />
      </header>
      {mode === 'single' ? <div className="resourceCollectionSingle">{children}</div> :
        <div ref={collectionRef} className={'resourceCollectionItems is' + (mode === 'grid' ? 'Grid' : 'List') + (selectable ? ' isSelectable' : '')}>
          {items.map((item) => <article className="resourceCollectionItem" data-resource-id={item.id} key={item.id}>
            {selectable && <Checkbox aria-label={item.selectionLabel} checked={selectedIds.includes(item.id)}
              onChange={(event) => onToggle(item.id, event.target.checked)} />}
            <button type="button" className="resourceItemBody" aria-label={'打开' + item.label}
              aria-describedby={`${statusId}-${item.id}`} onClick={() => onOpen(item.id)}>
              <span className="resourceItemImage">
                {item.image ? <img src={item.image} alt=""
                  onLoad={() => item.imageCandidate && onCandidateState?.(item.id, item.image!, true)}
                  onError={() => item.imageCandidate && onCandidateState?.(item.id, item.image!, false)} /> : <ImageIcon size={24} />}
              </span>
              <span className="resourceItemText"><strong>{item.label}</strong>
                <span className="resourceItemDescription">{item.description}</span>
                <span className="resourceItemStatus" id={`${statusId}-${item.id}`}>
                  {Array.isArray(item.status) ? item.status.map((line) => <span className="resourceItemStatusLine" key={line}>{line}</span>) : item.status}
                </span>
              </span>
            </button>
          </article>)}
          {!items.length && <div className="resourceCollectionEmpty">没有匹配的内容</div>}
        </div>}
    </section>
  );
}
