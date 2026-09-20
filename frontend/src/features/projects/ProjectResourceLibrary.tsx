import { useEffect, useState } from "react";
import { Button, Input } from "antd";
import { AssetCard } from "../assets/AssetCard";
import { AssetForm } from "../assets/AssetForm";
import type { AssetKind, GlobalAsset } from "../assets/asset-model";
import { Dialog } from "../../components/ui/Dialog";
import {
  readProjectResources,
  saveProjectResources,
  validAsset,
} from "./project-detail-model";

const kinds: { key: AssetKind; label: string }[] = [
  { key: "character", label: "角色" },
  { key: "scene", label: "场景" },
  { key: "prop", label: "道具" },
];

function readGlobalAssets(): GlobalAsset[] {
  try {
    const value: unknown = JSON.parse(
      localStorage.getItem("avi-global-assets-v1") ?? "[]",
    );
    return Array.isArray(value) ? value.filter(validAsset) : [];
  } catch {
    return [];
  }
}

export function ProjectResourceLibrary({ projectId }: { projectId: string }) {
  const [resources, setResources] = useState(() =>
    readProjectResources(projectId),
  );
  const [kind, setKind] = useState<AssetKind>("character");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    setResources(readProjectResources(projectId));
  }, [projectId]);
  function update(next: GlobalAsset[]) {
    saveProjectResources(projectId, next);
    setResources(next);
  }

  const label = kinds.find((item) => item.key === kind)!.label;
  const visible = resources.filter(
    (resource) =>
      resource.kind === kind &&
      [
        resource.name,
        resource.description,
        resource.category,
        ...(resource.tags ?? []),
      ].some((text) =>
        text.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
      ),
  );
  const available = readGlobalAssets().filter(
    (asset) =>
      asset.kind === kind &&
      !resources.some((resource) => resource.id === asset.id),
  );

  return (
    <section
      className="overview-card project-resource-library"
      aria-labelledby="project-resources-title"
    >
      <div className="overview-heading">
        <div>
          <h2 id="project-resources-title">资源库</h2>
          <p>本剧使用的角色、场景与道具</p>
        </div>
      </div>
      <div className="resource-toolbar">
        <div
          className="resource-kind-tabs"
          role="tablist"
          aria-label="本剧资源类型"
        >
          {kinds.map((item) => (
            <button
              key={item.key}
              role="tab"
              aria-selected={kind === item.key}
              className={kind === item.key ? "active" : ""}
              onClick={() => {
                setKind(item.key);
                setQuery("");
              }}
            >
              {item.label}
              <span>
                {
                  resources.filter((resource) => resource.kind === item.key)
                    .length
                }
              </span>
            </button>
          ))}
        </div>
        <div className="resource-actions">
          <label className="resource-search">
            <span className="sr-only">搜索本剧{label}</span>
            <Input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={`搜索${label}`}
            />
          </label>
          <Button onClick={() => setImporting(true)}>从全局素材库添加</Button>
          <Button type="primary" onClick={() => setCreating(true)}>
            新建{label}
          </Button>
        </div>
      </div>
      {visible.length ? (
        <div className="asset-grid">
          {visible.map((asset) => (
            <AssetCard
              key={asset.id}
              asset={asset}
              onRemove={(id) =>
                update(resources.filter((resource) => resource.id !== id))
              }
            />
          ))}
        </div>
      ) : (
        <div className="resource-empty">
          {query
            ? `没有找到匹配的${label}`
            : `本剧还没有${label}，可从全局素材库添加或直接新建。`}
        </div>
      )}
      {creating && (
        <AssetForm
          kind={kind}
          onClose={() => setCreating(false)}
          onSave={(asset) => {
            update([asset, ...resources]);
            setCreating(false);
          }}
        />
      )}
      {importing && (
        <Dialog
          title={`从全局素材库添加${label}`}
          onClose={() => setImporting(false)}
        >
          <div className="resource-import">
            {available.length ? (
              available.map((asset) => (
                <div className="resource-import-row" key={asset.id}>
                  <div>
                    <strong>{asset.name}</strong>
                    <p>{asset.description || "暂无描述"}</p>
                  </div>
                  <Button
                    type="primary"
                    onClick={() => {
                      update([...resources, asset]);
                      setImporting(false);
                    }}
                  >
                    添加到本剧
                  </Button>
                </div>
              ))
            ) : (
              <p>
                全局素材库中暂无可添加的{label}。可以返回主菜单的素材库先创建。
              </p>
            )}
            <div className="dialog-actions">
              <Button onClick={() => setImporting(false)}>完成</Button>
            </div>
          </div>
        </Dialog>
      )}
    </section>
  );
}
