import { useState } from 'react';
import { Select } from 'antd';
import type { AssetRead } from '../../api/modules/assets';

function AssetLabel({ asset }: { asset: AssetRead }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  return <span className="shot-asset-label" title={asset.name}>
    <span className="shot-asset-thumbnail">{asset.image?.url && failedUrl !== asset.image.url
      ? <img src={asset.image.url} alt="" loading="lazy" onError={() => setFailedUrl(asset.image!.url)}/>
      : <span>{asset.image?.url ? '失效' : '无图'}</span>}</span>
    <span className="shot-asset-name">{asset.name}</span>
  </span>;
}

export function ShotAssetPicker({ assets, assetIds, disabled, onChange }: {
  assets: AssetRead[]; assetIds: string[]; disabled: boolean; onChange: (ids: string[]) => void;
}) {
  return <div className="shot-asset-picker" role="group" aria-label="关联素材">
    {(['character', 'prop', 'scene'] as const).map(kind => {
      const name = { character: '角色', prop: '道具', scene: '场景' }[kind];
      const options = assets.filter(asset => asset.kind === kind);
      const ids = new Set(options.map(asset => asset.id));
      return <div className="shot-asset-row" key={kind}>
        <span>{name}</span>
        <Select mode="multiple" showSearch optionFilterProp="name" aria-label={`关联${name}`}
          placeholder={`选择${name}`} disabled={disabled} value={assetIds.filter(id => ids.has(id))}
          options={options.map(asset => ({ value: asset.id, name: asset.name, label: <AssetLabel asset={asset}/> }))}
          notFoundContent={`暂无匹配${name}，可在素材准备中添加`}
          onChange={next => onChange([...assetIds.filter(id => !ids.has(id)), ...next])}/>
      </div>;
    })}
  </div>;
}
