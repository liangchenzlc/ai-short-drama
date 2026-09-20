import type { MediaRef } from "../projects/episode-workflow";

export type AssetKind = "character" | "scene" | "prop";

export type AssetVisualRef = MediaRef & {
  kind: "demo-image" | "project-image";
};

export interface GlobalAsset {
  id: string;
  kind: AssetKind;
  name: string;
  description: string;
  category: string;
  tags: string[];
  createdAt: string;
  visualRef?: AssetVisualRef;
}

export function createAsset(
  kind: AssetKind,
  input: { name: string; description: string; category?: string; tags: string },
): GlobalAsset {
  const name = input.name.trim();
  if (!name) throw new Error("请输入素材名称");
  return {
    id: crypto.randomUUID(),
    kind,
    name,
    description: input.description.trim(),
    category: input.category?.trim() ?? "",
    tags: input.tags
      .split(/[,，]/)
      .map((tag) => tag.trim())
      .filter(Boolean),
    createdAt: new Date().toISOString(),
  };
}

export function filterAssets(
  assets: GlobalAsset[],
  kind: AssetKind,
  query: string,
): GlobalAsset[] {
  const keyword = query.trim().toLocaleLowerCase();
  return assets.filter(
    (item) =>
      item.kind === kind &&
      (!keyword ||
        [item.name, item.description, item.category, ...item.tags].some(
          (value) => value.toLocaleLowerCase().includes(keyword),
        )),
  );
}
