import { useEffect, useState } from "react";
import { Button, Input } from "antd";
import { AssetCard } from "../../features/assets/AssetCard";
import { AssetForm } from "../../features/assets/AssetForm";
import {
  filterAssets,
  type AssetKind,
  type GlobalAsset,
} from "../../features/assets/asset-model";

const storageKey = "avi-global-assets-v1";
const names = { character: "角色", scene: "场景", prop: "道具" };
function loadAssets(): GlobalAsset[] {
  try {
    const parsed: unknown = JSON.parse(
      localStorage.getItem(storageKey) ?? "[]",
    );
    return Array.isArray(parsed)
      ? (parsed.filter(
          (item) =>
            typeof item?.name === "string" &&
            ["character", "scene", "prop"].includes(item.kind),
        ) as GlobalAsset[])
      : [];
  } catch {
    return [];
  }
}
export function AssetsPage({ kind }: { kind: AssetKind }) {
  const [assets, setAssets] = useState(loadAssets);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(assets));
  }, [assets]);
  const matches = filterAssets(assets, kind, query);
  return (
    <section className="studio-page" aria-labelledby="asset-title">
      <div className="studio-page-head">
        <div>
          <h1 id="asset-title">{names[kind]}素材</h1>
          <p>跨项目整理和查找{names[kind]}，内容保存在当前浏览器中。</p>
        </div>
        <Button type="primary" onClick={() => setCreating(true)}>
          新建{names[kind]}
        </Button>
      </div>
      <div className="studio-toolbar">
        <label className="studio-search">
          <span className="sr-only">搜索{names[kind]}</span>
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`搜索${names[kind]}名称、描述或标签`}
          />
        </label>
        <span>共 {matches.length} 项</span>
      </div>
      {matches.length ? (
        <div className="asset-grid">
          {matches.map((asset) => (
            <AssetCard
              key={asset.id}
              asset={asset}
              onRemove={(id) =>
                setAssets((items) => items.filter((item) => item.id !== id))
              }
            />
          ))}
        </div>
      ) : (
        <div className="studio-empty">
          <h2>{query ? "未找到匹配的素材" : `还没有${names[kind]}素材`}</h2>
          <p>
            {query
              ? "试试其他关键词，或清空搜索。"
              : `点击右上角「新建${names[kind]}」开始整理。`}
          </p>
        </div>
      )}
      {creating && (
        <AssetForm
          kind={kind}
          onClose={() => setCreating(false)}
          onSave={(asset) => {
            setAssets((items) => [asset, ...items]);
            setCreating(false);
          }}
        />
      )}
    </section>
  );
}
