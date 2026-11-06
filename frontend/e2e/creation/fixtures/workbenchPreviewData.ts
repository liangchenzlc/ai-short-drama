import type { AssetView, EpisodeView, ShotView, StoryView } from '@host/types/creation/workspace/workbenchView';
import type { ImageGroup, ImageMedia } from '@host/types/media/image-groups';

const media = (id: string, file: string): ImageMedia => ({
  media_id: id, url: '/.runtime/workbenchPreview/' + file, width: 800, height: 800,
});

const group = (id: string, target: 'asset' | 'shot', targetId: string, images: ImageMedia[]): ImageGroup => ({
  id, kind: 'generation', status: 'completed', target_kind: target, target_id: targetId,
  target_version: '1', is_current: true, expected_count: images.length, primary_index: 0,
  primary_version: '1', notice: null, task_id: null, created_at: '',
  items: images.map((image, index) => ({
    id: id + String(index + 1), output_index: index, ...image, item_version: '1',
    status: 'ready', error: null, prompt: null, history: [],
  })),
});

export function createPreviewData(scenario: string) {
  const woman = media('101', 'woman.jpg');
  const womanAlternative = media('102', 'camera.jpg');
  const street = media('103', 'street.jpg');
  const shotImage = media('104', 'street.jpg');
  const story: StoryView = { genre: '都市悬疑', core: '一封迟到的信，揭开一段被掩埋的往事。', outline: '' };
  const episodes: EpisodeView[] = [
    { id: '1', title: '', summary: '', script: scenario === 'single' ? '单集剧本原稿' : '林夏推开咖啡馆的门，发现桌上留着一封没有署名的信。' },
  ];
  if (scenario === 'series') episodes.push({ id: '2', title: '', summary: '林夏沿着信中的线索寻找真相。', script: '夜晚，林夏再次来到街角。' });
  const characterGroup = group('201', 'asset', '1', [woman, womanAlternative]);
  const sceneGroup = group('202', 'asset', '2', [street]);
  const assets: AssetView[] = [
    { id: '1', kind: 'character', name: '林夏', description: '追寻真相的年轻记者。', tags: ['第 01 集'],
      profile: { role: '主角', gender: '女', age: '28', identity: '记者', appearance: '黑色短发，目光坚定。', personality: '冷静、敏锐，富有好奇心。' },
      imageGroups: [characterGroup], activeGroupId: characterGroup.id, adoptedImage: woman, imagePrompt: '都市记者，电影感自然光' },
    { id: '2', kind: 'scene', name: '咖啡馆', description: '街角安静的咖啡馆。', tags: ['第 01 集'], profile: null,
      imageGroups: [sceneGroup], activeGroupId: sceneGroup.id, adoptedImage: street, imagePrompt: '雨后街角的咖啡馆' },
    { id: '3', kind: 'prop', name: '信封', description: '一封没有署名的旧信。', tags: [], profile: null,
      imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '' },
  ];
  const shotGroup = group('203', 'shot', '1', [shotImage]);
  const shots: ShotView[] = [
    { id: '1', episodeId: '1', position: 1, title: '走进咖啡馆', script: '林夏推门而入，目光落在桌上的信封。', shotSize: 'medium_shot', durationMs: 5000,
      assetIds: ['1', '2'], imageGroups: [shotGroup], activeGroupId: shotGroup.id, adoptedImage: shotImage,
      imagePrompt: '林夏走进咖啡馆，柔和逆光。', videoPrompt: '镜头缓慢推进，林夏走向桌边。',
      videoReferences: [{ type: 'asset', assetId: '1' }, { type: 'asset', assetId: '2' }], video: null },
    { id: '2', episodeId: '1', position: 2, title: '桌上的信', script: '特写：林夏拿起信封。', shotSize: 'close_up', durationMs: 4000,
      assetIds: ['1', '3'], imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '', videoPrompt: '',
      videoReferences: [{ type: 'asset', assetId: '1' }], video: null },
    { id: '3', episodeId: '2', position: 1, title: '街角重逢', script: '林夏站在夜色中的街角。', shotSize: 'wide_shot', durationMs: 5000,
      assetIds: ['1', '2'], imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '', videoPrompt: '',
      videoReferences: [{ type: 'asset', assetId: '1' }, { type: 'asset', assetId: '2' }], video: null },
  ];
  if (scenario === 'batch') {
    assets.splice(0, assets.length, ...Array.from({ length: 101 }, (_, index): AssetView => ({
      id: String(index + 1), kind: 'character', name: '角色 ' + String(index + 1).padStart(3, '0'), description: '',
      tags: ['第 01 集'], profile: { role: '', gender: '', age: '', identity: '', appearance: '', personality: '' },
      imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '',
    })));
  }
  return { story, episodes, assets, shots };
}
export { group, media };
