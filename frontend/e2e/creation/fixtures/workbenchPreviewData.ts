import type { AssetView, ShotView } from '@host/types/creation/workspace/workbenchView';
import type { StorySettingsDocument } from '@host/types/creation/writing/documents';
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
  const story: StorySettingsDocument = { genre: '都市悬疑', logline: '一封迟到的信，揭开一段被掩埋的往事。', synopsis: '', character_bios: '' };
  const script = '林夏推开咖啡馆的门，发现桌上留着一封没有署名的信。';
  const characterGroup = group('201', 'asset', '1', [woman, womanAlternative]);
  const sceneGroup = group('202', 'asset', '2', [street]);
  if (scenario === 'partial') {
    characterGroup.status = 'partial';
    characterGroup.items[1] = { ...characterGroup.items[1], media_id: null, url: null,
      status: 'failed', error: '模拟生成失败，请重试' };
  }
  const assets: AssetView[] = [
    { id: '1', kind: 'character', name: '林夏', description: '追寻真相的年轻记者。',
      profile: { role: '主角', gender: '女', age: '28', identity: '记者', appearance: '黑色短发，目光坚定。', personality: '冷静、敏锐，富有好奇心。' },
      imageGroups: [characterGroup], activeGroupId: characterGroup.id, adoptedImage: woman, imagePrompt: '都市记者，电影感自然光' },
    { id: '2', kind: 'scene', name: '咖啡馆', description: '街角安静的咖啡馆。',
      profile: { scene_type: '室内', location: '街角', time: '傍晚', lighting: '暖色灯光', atmosphere: '安静、略显神秘', environment: '靠窗的木桌与旧书架' },
      imageGroups: [sceneGroup], activeGroupId: sceneGroup.id, adoptedImage: street, imagePrompt: '雨后街角的咖啡馆' },
    { id: '3', kind: 'prop', name: '信封', description: '一封没有署名的旧信。',
      profile: { prop_type: '信件', material: '牛皮纸', color: '米黄', size: '', condition: '略微陈旧', appearance: '边角磨损，无署名', purpose: '传递故事线索' },
      imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '' },
  ];
  const shotGroup = group('203', 'shot', '1', [shotImage]);
  const shots: ShotView[] = [
    { id: '1', position: 1, title: '走进咖啡馆', script: '林夏推门而入，目光落在桌上的信封。', shotSize: 'medium_shot', durationMs: 5000,
      cameraAngle: '平视', cameraMovement: '缓慢推进', dialogue: '', sound: '门铃轻响',
      assetIds: ['1', '2'], imageGroups: [shotGroup], activeGroupId: shotGroup.id, adoptedImage: shotImage,
      imagePrompt: '林夏走进咖啡馆，柔和逆光。', videoPrompt: '镜头缓慢推进，林夏走向桌边。',
      videoReferences: [{ type: 'asset', assetId: '1' }, { type: 'asset', assetId: '2' }], video: null },
    { id: '2', position: 2, title: '桌上的信', script: '特写：林夏拿起信封。', shotSize: 'close_up', durationMs: 4000,
      cameraAngle: '俯视', cameraMovement: '固定', dialogue: '', sound: '纸张摩擦声',
      assetIds: ['1', '3'], imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '', videoPrompt: '',
      videoReferences: [{ type: 'asset', assetId: '1' }], video: null },
  ];
  if (scenario === 'referenceLimit') {
    for (let index = 0; index < 3; index++) {
      const id = String(index + 4);
      const image = media(String(index + 110), 'woman.jpg');
      const imageGroup = group(String(index + 210), 'asset', id, [image]);
      assets.push({ ...assets[0], id, name: '参考角色 ' + (index + 1), imageGroups: [imageGroup],
        activeGroupId: imageGroup.id, adoptedImage: image });
      shots[0].assetIds.push(id);
      shots[0].videoReferences.push({ type: 'asset', assetId: id });
    }
  }
  if (scenario === 'batch') {
    assets.splice(0, assets.length, ...Array.from({ length: 101 }, (_, index): AssetView => ({
      id: String(index + 1), kind: 'character', name: '角色 ' + String(index + 1).padStart(3, '0'), description: '',
      profile: { role: '', gender: '', age: '', identity: '', appearance: '', personality: '' },
      imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '',
    })));
  }
  return { story, script, assets, shots };
}
export { group, media };
