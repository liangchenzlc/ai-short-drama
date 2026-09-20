import type { GlobalAsset } from "./asset-model";
import { Button, Card } from "antd";
import { Icon } from '../../components/ui/Icon';

export function AssetCard({
  asset,
  onRemove,
}: {
  asset: GlobalAsset;
  onRemove: (id: string) => void;
}) {
  return (
    <Card className="asset-card">
      <div
        className={`asset-cover asset-cover-${asset.kind}`}
        aria-hidden="true"
      >
        <span>
          <Icon name={asset.kind === 'character' ? 'person' : asset.kind} size={28} />
        </span>
      </div>
      <div className="asset-card-content">
        <h3>{asset.name}</h3>
        <p>{asset.description || "暂无描述"}</p>
        <div className="asset-card-meta">
          <span>{asset.category || "未分类"}</span>
          {asset.tags.slice(0, 2).map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
        </div>
      </div>
      <Button
        className="asset-remove"
        type="link"
        onClick={() => onRemove(asset.id)}
        aria-label={`删除${asset.name}`}
      >
        删除
      </Button>
    </Card>
  );
}
