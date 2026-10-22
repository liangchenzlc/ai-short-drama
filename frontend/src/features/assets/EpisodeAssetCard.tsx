import { useRef, type RefCallback } from 'react';
import { Button, Checkbox, Dropdown } from 'antd';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { Icon } from '../../components/ui/Icon';
import type { LibraryAssetRead } from '../../api/modules/assets';
import type { LocalAssetDraft } from './episode-asset-drafts';
import { Check, Image as ImageIcon } from 'lucide-react';

export function StandardEpisodeAssetCard({ asset, selected, disabled, readOnly, removing, entryRef, onOpen, onRemove }: {
  asset: LibraryAssetRead | LocalAssetDraft; selected: boolean; disabled: boolean;
  readOnly: boolean; removing: boolean; entryRef: RefCallback<HTMLButtonElement>;
  onOpen: () => void; onRemove: () => void;
}) {
  const image = 'image' in asset ? asset.image : null;
  const labels = { character: '角色', scene: '场景', prop: '道具' };
  const pendingCreation = 'status' in asset && ['saving', 'unknown'].includes(asset.status);
  const moreTrigger = useRef<HTMLButtonElement | null>(null);
  return <article className={`standard-asset-card${selected ? ' is-selected' : ''}`}>
    <button type="button" ref={entryRef} className="standard-asset-card-button" aria-label={asset.name || '未命名素材'} aria-pressed={selected} disabled={disabled} onClick={onOpen}>
      <span className="standard-asset-card-media">{image?.url ? <img src={image.url} alt="" loading="lazy" onError={event => { event.currentTarget.hidden = true; }}/> : <ImageIcon size={27} aria-hidden="true"/>}
        {!image && <span className="standard-asset-pending">待制作</span>}
        {selected && <span className="standard-asset-selected" aria-hidden="true"><Check size={13}/></span>}
      </span>
      <span className="standard-asset-card-caption"><strong title={asset.name}>{asset.name || '未命名素材'}</strong><small>{asset.kind ? labels[asset.kind] : '选择类型'}</small></span>
    </button>
    {!readOnly && <Dropdown trigger={['click']} menu={{
      items: [{ key: 'remove', label: '删除素材', danger: true, disabled: pendingCreation }],
      onClick: ({ domEvent }) => { domEvent.stopPropagation(); moreTrigger.current?.focus(); onRemove(); },
    }}>
      <Button ref={moreTrigger} className="standard-asset-card-more" type="text" aria-label={`${asset.name || '未命名素材'}卡片更多操作`} title={pendingCreation ? '请先核对原创建请求' : '更多操作'} disabled={disabled} loading={removing} icon={<Icon name="more" size={16}/>} onClick={event => event.stopPropagation()}/>
    </Dropdown>}
  </article>;
}

export function EpisodeAssetCard({ asset, selected, disabled, readOnly, canShare, batchEnabled, batchChecked, entryRef, onOpen, onEdit, onShare, onRemove, onBatchChange }: {
  asset: LibraryAssetRead;
  selected: boolean;
  disabled: boolean;
  readOnly: boolean;
  canShare: boolean;
  batchEnabled: boolean;
  batchChecked: boolean;
  entryRef: RefCallback<HTMLButtonElement>;
  onOpen: () => void;
  onEdit: () => void;
  onShare: () => void;
  onRemove: () => void;
  onBatchChange: (checked: boolean) => void;
}) {
  return <article className={`asset-card library-resource-card episode-asset-card${selected ? ' is-selected' : ''}`}>
    {batchEnabled && !readOnly && <Checkbox className="batch-item-select" aria-label={`批量选择 ${asset.name}`} checked={batchChecked} disabled={disabled} onChange={event => onBatchChange(event.target.checked)}>批量选择</Checkbox>}
    {asset.image?.url ? <PreviewImage triggerClassName="resource-image" src={asset.image.url} alt={asset.name}/> : <button type="button" className="resource-image" disabled={disabled} aria-label={`查看 ${asset.name} 的详情`} onClick={onOpen}><span><Icon name={asset.kind === 'character' ? 'person' : asset.kind} size={28}/>暂无图片</span></button>}
    <div className="asset-card-content">
      <h3><button type="button" className="asset-name-button" ref={entryRef} disabled={disabled} onClick={onOpen}>{asset.name}</button></h3>
      <p>{asset.description || asset.prompt || '补充外观或特征，方便后续创作。'}</p>
      <div className="resource-meta"><span className={`status-badge ${asset.state === 'confirmed' ? 'is-success' : 'is-pending'}`}>{asset.state === 'confirmed' ? '已确认' : '待确认'}</span><small>{asset.reference_count} 处引用</small></div>
    </div>
    {!readOnly && <Dropdown trigger={['click']} menu={{ items: [{ key: 'edit', label: '编辑素材' }, ...(canShare ? [{ key: 'share', label: '共享到项目' }] : []), { key: 'remove', label: '从本集删除', danger: true }], onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); if (key === 'edit') onEdit(); else if (key === 'share') onShare(); else onRemove(); } }}>
      <Button className="episode-asset-more" type="text" aria-label={`${asset.name}更多操作`} disabled={disabled} icon={<Icon name="more" size={18}/>} onClick={event => event.stopPropagation()}/>
    </Dropdown>}
  </article>;
}
