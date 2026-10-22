export type AssetPromptReference = { media_id: string; url: string | null; name: string };

export function combineAssetPromptReferences(shared: readonly AssetPromptReference[], privateReferences: readonly AssetPromptReference[]): AssetPromptReference[] {
  const seen = new Set<string>();
  return [...shared, ...privateReferences].filter(reference => {
    if (seen.has(reference.media_id)) return false;
    seen.add(reference.media_id);
    return true;
  });
}

export function remapAssetPromptReferences(value: string, before: readonly AssetPromptReference[], after: readonly AssetPromptReference[]): string {
  const previous = combineAssetPromptReferences(before, []);
  const next = new Map(combineAssetPromptReferences(after, []).map((reference, index) => [reference.media_id, index + 1]));
  return value.replace(/@图片([1-9]\d*)(?!\d)/gu, (token, order: string) => {
    const reference = previous[Number(order) - 1];
    if (!reference) return token;
    const position = next.get(reference.media_id);
    return position === undefined ? '' : `@图片${position}`;
  });
}
