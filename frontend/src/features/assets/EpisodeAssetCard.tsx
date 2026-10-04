import type { RefCallback } from 'react';
import { Button, Checkbox, Dropdown } from 'antd';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { Icon } from '../../components/ui/Icon';
import type { LibraryAssetRead } from '../../api/modules/assets';

export function EpisodeAssetCard({ asset, selected, disabled, readOnly, canShare, batchEnabled, batchChecked, entryRef, onOpen, onShare, onRemove, onBatchChange }: {
  asset: LibraryAssetRead;
  selected: boolean;
  disabled: boolean;
  readOnly: boolean;
  canShare: boolean;
  batchEnabled: boolean;
  batchChecked: boolean;
  entryRef: RefCallback<HTMLButtonElement>;
  onOpen: () => void;
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
    {!readOnly && <Dropdown trigger={['click']} menu={{ items: [...(canShare ? [{ key: 'share', label: '共享到项目' }] : []), { key: 'remove', label: '从本集删除', danger: true }], onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); if (key === 'share') onShare(); else onRemove(); } }}>
      <Button className="episode-asset-more" type="text" aria-label={`${asset.name}更多操作`} disabled={disabled} icon={<Icon name="more" size={18}/>} onClick={event => event.stopPropagation()}/>
    </Dropdown>}
  </article>;
}
