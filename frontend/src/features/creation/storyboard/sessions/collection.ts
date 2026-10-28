import type { StoryboardApi, StoryboardPage } from '@host/types/creation/storyboard/shots';
export async function loadLoadedShots(api: StoryboardApi, signal: AbortSignal, count: number): Promise<StoryboardPage> {
  const first = await api.shots(signal, false, 0);
  const items = [...first.items];
  while (items.length < Math.min(count, first.total)) {
    const next = await api.shots(signal, false, items.length);
    if (next.storyboard_version !== first.storyboard_version) throw new Error('分镜列表已变化，请重新加载。');
    if (!next.items.length) break;
    items.push(...next.items);
  }
  return { ...first, items };
}
