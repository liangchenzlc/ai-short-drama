import type { WebProject } from '../types/projects';
import { saveEpisodes, saveProjectDetails, saveProjectResources } from '../features/projects/project-detail-model';
import { emptyWorkflow, saveWorkflow } from '../features/projects/episode-workflow';
import { sampleAssets, sampleShots, sampleImage, DEMO_MODELS } from '../features/projects/episode-demo';
import type { GlobalAsset } from '../features/assets/asset-model';

const projectsKey = 'drama-web-projects-v1';
export function readProjects(): WebProject[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(projectsKey) ?? '[]');
    return Array.isArray(saved) ? saved.filter((p): p is WebProject =>
      typeof p?.projectId === 'string' && typeof p.name === 'string' &&
      ['16:9', '9:16'].includes(p.aspect) && typeof p.targetMs === 'number') : [];
  } catch { return []; }
}
export function writeProjects(items: WebProject[]) { localStorage.setItem(projectsKey, JSON.stringify(items)); }
export function seedDemo() {
  if (localStorage.getItem(projectsKey) !== null) return;
  const projects: WebProject[] = [
    { projectId: 'demo-rain', name: '雨夜借光', aspect: '16:9', targetMs: 60000, lastOpenedAt: '2026-09-18T08:00:00Z' },
    { projectId: 'demo-summer', name: '夏日来信', aspect: '9:16', targetMs: 90000, lastOpenedAt: '2026-09-17T08:00:00Z' },
    { projectId: 'demo-city', name: '城市的另一面', aspect: '16:9', targetMs: 120000, lastOpenedAt: '2026-09-16T08:00:00Z' },
  ];
  const synopses = [
    '一场突如其来的雨，让调查员林小雨走进旧城深处。她借来一盏铜灯，循着一张旧地图寻找失踪的朋友，却发现灯的主人早已知道答案。',
    '返乡的年轻插画师在旧书店发现一封未寄出的信。循着信中的地址，她重新遇见那个改变了自己整个夏天的人。',
    '最后一班地铁离站后，深夜电台收到一通奇怪的电话。主播沿着声音寻找真相，看见了城市里那些被遗忘的故事。',
  ];
  const resources: GlobalAsset[] = sampleAssets().map((a) => ({ id: a.id, kind: a.kind, name: a.name, description: a.description, category: a.kind === 'character' ? '主角' : a.kind === 'scene' ? '外景' : '故事道具', tags: ['雨夜借光', '电影写实'], createdAt: '2026-09-18T08:00:00Z', visualRef: { kind: 'demo-image', id: sampleImage(a.id).id } }));
  projects.forEach((p, index) => {
    saveProjectDetails(p.projectId, { style: index === 1 ? '清透水彩，暖色胶片' : '都市写实，电影质感', synopsis: synopses[index], aspect: p.aspect });
    saveEpisodes(p.projectId, [
      { id: 'episode-1', title: ['雨巷来客', '未寄出的信', '末班列车'][index], synopsis: synopses[index] },
      { id: 'episode-2', title: ['灯下的秘密', '沿海公路', '陌生来电'][index], synopsis: '故事继续，新的线索让人物做出意想不到的选择。' },
    ]);
    saveProjectResources(p.projectId, resources);
    const workflow = emptyWorkflow({ aspect: p.aspect });
    workflow.novel = synopses[index] + '\n\n雨停之前，她终于在巷口看见那盏熟悉的灯。门后的声音轻轻说：“你终于来了。”';
    workflow.scriptDraft = `第一场 · ${['旧城雨巷', '夏日书店', '地铁站台'][index]} · 夜\n\n${synopses[index]}\n\n人物停下脚步，抬头看向那扇门。\n“有人在吗？”\n\n镜头缓缓推近，一束暖光从门缝透出。`;
    workflow.style = '写实电影质感';
    workflow.models = Object.fromEntries(Object.entries(DEMO_MODELS).map(([key, items]) => [key, items[0].value])) as typeof workflow.models;
    workflow.assets = sampleAssets().map((asset) => ({ ...asset, imageCandidates: [{ id: `candidate-${asset.id}`, source: 'demo', value: sampleImage(asset.id) }], selectedImageId: `candidate-${asset.id}` }));
    workflow.shots = sampleShots(workflow.scriptDraft, workflow.assets);
    workflow.reviews = { source: 'review', script: 'review', assets: 'review', storyboard: 'review', video: 'not_started' };
    saveWorkflow(p.projectId, 'episode-1', workflow);
  });
  if (!localStorage.getItem('avi-global-assets-v1')) localStorage.setItem('avi-global-assets-v1', JSON.stringify(resources));
  writeProjects(projects);
}

