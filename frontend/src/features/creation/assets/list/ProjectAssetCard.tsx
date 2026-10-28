import { Icon } from '@host/shared/ui/Icon';
import type { LibraryAssetRead } from '@host/types/creation/assets/management';
import type { LocalAssetDraft } from '@host/types/creation/assets/sessions/local-asset-draft-model';
import { Button, Dropdown } from 'antd';
import { Check, Image as ImageIcon } from 'lucide-react';
import { useRef, useState, type RefCallback } from 'react';
export function ProjectAssetCard({
  asset,
  selected,
  disabled,
  readOnly,
  removing,
  entryRef,
  onOpen,
  onRemove,
}: {
  asset: LibraryAssetRead | LocalAssetDraft;
  selected: boolean;
  disabled: boolean;
  readOnly: boolean;
  removing: boolean;
  entryRef: RefCallback<HTMLButtonElement>;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const image = 'image' in asset ? asset.image : null;
  const pendingCreation = 'status' in asset && ['saving', 'unknown'].includes(asset.status);
  const moreTrigger = useRef<HTMLButtonElement | null>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  return (
    <article className={`standard-asset-card${selected ? ' is-selected' : ''}`}>
      <button
        type="button"
        ref={entryRef}
        className="standard-asset-card-button"
        aria-label={asset.name || '未命名素材'}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onOpen}
      >
        <span className="standard-asset-card-media">
          {image?.url ? (
            <img
              src={image.url}
              alt=""
              loading="lazy"
              onError={(event) => {
                event.currentTarget.hidden = true;
              }}
            />
          ) : (
            <ImageIcon size={27} aria-hidden="true" />
          )}
          {!image && <span className="standard-asset-pending">待制作</span>}
          {selected && (
            <span className="standard-asset-selected" aria-hidden="true">
              <Check size={13} />
            </span>
          )}
        </span>
        <span className="standard-asset-card-caption">
          <strong title={asset.name}>{asset.name || '未命名素材'}</strong>
        </span>
      </button>
      {!readOnly && (
        <Dropdown
          trigger={['click']}
          open={actionsOpen}
          onOpenChange={setActionsOpen}
          menu={{
            items: [
              {
                key: 'remove',
                label: '删除素材',
                danger: true,
                disabled: pendingCreation,
              },
            ],
            onClick: ({ domEvent }) => {
              domEvent.stopPropagation();
              setActionsOpen(false);
              moreTrigger.current?.focus();
              onRemove();
            },
          }}
        >
          <Button
            ref={moreTrigger}
            className="standard-asset-card-more"
            type="text"
            aria-label={`${asset.name || '未命名素材'}卡片更多操作`}
            aria-expanded={actionsOpen}
            title={pendingCreation ? '请先核对原创建请求' : '更多操作'}
            disabled={disabled}
            loading={removing}
            icon={<Icon name="more" size={16} />}
            onClick={(event) => event.stopPropagation()}
          />
        </Dropdown>
      )}
    </article>
  );
}
