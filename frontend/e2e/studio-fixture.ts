import type { Page } from '@playwright/test';

export const root = '/projects/10/episodes/20';
const time = '2026-09-24T00:00:00Z';
export const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';

export async function fixture(page: Page, ui = false, origin = 'http://127.0.0.1:4175') {
  const requests: { path: string; method: string; query: URLSearchParams; body: any }[] = [];
  const unexpected: string[] = [];
  const errors: string[] = [];
  const modelPreferences = new Map<string, string>();
  const project = { id: '10', name: '雨夜来信', synopsis: '一封迟到的信改变了两个人的命运。', style: '电影质感', aspect: '16:9', episode_count: 1 };
  const episode = { id: '20', project_id: '10', position: 1, episode_number: 1, title: '归来的旅人', synopsis: '林晚走进雨夜。', style: '电影质感', aspect: '16:9' };
  const writing = { episode_id: '20', content_version: '1', novel: { id: '30', content: '雨夜，林晚拿着一封旧信走入车站。', updated_at: time }, editing_script: { id: '40', content: '林晚走进车站，抬头寻找站台。', state: 'confirmed', updated_at: time }, confirmed_script_id: '40' as string | null };
  const asset = { id: '501', kind: 'character', name: '林晚', label: '主角', description: '黑发，穿着深色风衣。', prompt: '人物肖像，深色风衣', tags: [], scene_time: '', state: 'unconfirmed', row_version: '1', media_id: null, image: null, reference_count: 1, link_id: '601', position: 1 };
  const assets = [asset];
  const shots: any[] = Array.from({ length: 80 }, (_, index) => ({ id: String(101 + index), position: index + 1, script: `林晚走过雨夜中的站台，镜头 ${index + 1}，列车的光线映在她的眼睛里。`, duration_ms: 3000, source_excerpt: '', asset_ids: ['501'], row_version: '1', context_hash: 'a'.repeat(64), image_settings: { aspect: 'inherit', resolution: '2K', layout: 'single' }, image: null, deleted_at: null }));
  let storyboardVersion = '1';
  const referenceState: Record<string, any[]> = {};
  const source = (scene: string) => ({ scene, project_id: '10', episode_id: '20', script_id: '40', content_version: '1' });
  const task = (id: string, scene: string) => ({ generation_id: id, service_type: scene.endsWith('image') ? 'image' : 'text', status: 'succeeded', source: scene === 'asset_image' ? { scene, asset_id: '501', row_version: '1', project_id: '10', episode_id: '20' } : source(scene), config: { id: '77', name: '测试替身模型', model_key: 'fixture', provider: 'fixture' }, created_at: time, can_cancel: false, can_retry: false, can_resume: false, display_context: { project: project.name, episode: '第 1 集：归来的旅人', subject: scene === 'asset_image' ? '角色：林晚' : '分镜脚本', scope: '通用任务' } });
  const textTasks = Array.from({ length: 35 }, (_, index) => task(String(8001 + index), 'script_shots'));
  const candidate = { id: '41', position: 2, state: 'unconfirmed', preview: '候选剧本：林晚在站台发现了信的主人。', content: '候选剧本：林晚在站台发现了信的主人。', is_editing: false, is_confirmed: false, generation_id: '77001', created_at: time, updated_at: time };
  const controls = { emptyProjects: false, errorProjects: false, delayProjects: 0 };
  const poster = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#242b3e"/><path d="M248 130h144v100H248z M278 130v100 M362 130v100" fill="none" stroke="#7e90c4" stroke-width="3"/><text x="320" y="270" fill="#b9c5e4" font-family="sans-serif" font-size="20" text-anchor="middle">测试参考画面</text></svg>').toString('base64');
  const projects = [project, { ...project, id: '11', name: '长街与少年', synopsis: '夏天结束之前，完成一场迟到的告别。', aspect: '9:16', style: '日系动画', episode_count: 3 }, { ...project, id: '12', name: '零点之后', synopsis: '午夜广播里藏着未被解开的谜题。', style: '悬疑写实', episode_count: 2 }, { ...project, id: '13', name: '漫长的星期天', synopsis: '', episode_count: 0 }].map(p => ({ ...p, last_opened_at: time, created_at: time }));
  const model = (kind: string) => ({ id: '77', name: '本地验收模型', model_key: `fixture-${kind}`, service_type: kind, provider: '测试替身', base_url: 'https://fixture.invalid/v1', enabled: 1, is_default: 1, is_deleted: 0, row_version: '1', has_api_key: true });
  const media = [0, 1, 2, 3].map((_, i) => ({ asset_id: String(7001 + i), generation_id: '8001', media_id: String(9901 + i), name: ['雨夜站台', '旅人侧影', '旧信细节', '远去的列车'][i], media_type: 'image', row_version: '1', url: poster, width: 1920, height: 1080, created_at: time, source: null }));
  const clips = [0, 1, 2].map((_, i) => ({ id: String(1001 + i), source_clip_id: String(1001 + i), shot_id: String(101 + i), media_id: String(9101 + i), position: i + 1, shot_position: i + 1, included: true, muted: false, trim_in_ms: 0, trim_out_ms: 3000, duration_ms: 3000, script: shots[i].script, url: null, poster, is_stale: false, archived: false, issue: null }));
  const assembly = { assembly: { id: '111', row_version: '1', aspect: '16:9', resolution: '1080p', current_media_id: null }, source_hash: 'a'.repeat(64), context_hash: 'a'.repeat(64), source_count: 3, clips, sources: clips, changes: [], jobs: [] };
  const sound = { mode: 'native', row_version: '1', timeline_hash: 'a'.repeat(64), duration_ms: 9000, needs_review: false, stale_lines: [], document: { dialogue: [], subtitles: [{ start_ms: 0, end_ms: 2800, text: '这封信，终于送到了。' }], native_ducking: [], music: null, original_volume: 1, dialogue_volume: 1, burn_subtitles: true, font_size: 24 }, media: {}, uploads: [], voice_defaults: { row_version: '1', voices: {} } };
  const batch = { id: '6001', scene: 'shot_image', config_id: '77', scope: { scene: 'shot_image', scope: { library: 'episode', project_id: '10', episode_id: '20' }, config_id: '77', mode: 'missing', count: 1 }, status: 'completed', counts: { succeeded: 3 }, total: 3, created_at: time };
  if (ui) {
    Object.assign(asset, { state: 'confirmed', media_id: '9901', image: { media_id: '9901', url: poster } });
    assets.push({ ...asset, id: '502', kind: 'scene', name: '雨夜车站', description: '旧车站的灯光洒在潮湿的站台上。' }, { ...asset, id: '503', kind: 'prop', name: '泛黄的旧信', description: '一封被反复打开的信，纸面已经褪色。' });
    for (const [i, shot] of shots.entries()) Object.assign(shot, { video_prompt: '镜头缓慢推进，旅人望向站台。', video_default_prompt: shot.script, video_settings: { resolution: '720p', duration_ms: 3000 }, video_context_hash: 'a'.repeat(64), video: null, image: i < 3 ? { media_id: String(9101 + i), media_asset_id: '7001', url: poster, width: 1920, height: 1080, layout: 'single', aspect: '16:9', resolution: '2K', is_stale: false } : null });
  }
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => { unexpected.push(`native dialog: ${dialog.message()}`); void dialog.dismiss(); });
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) { unexpected.push(request.url()); return route.abort(); }
    if (!url.pathname.startsWith('/api/v1/')) return route.continue();
    const path = url.pathname.slice('/api/v1'.length);
    const query = url.searchParams;
    const method = request.method();
    const body = request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
    requests.push({ path, method, query, body });
    const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const paged = (items: any[]) => { const offset = Number(query.get('offset') || 0); const limit = Number(query.get('limit') || 20); return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }; };
    if (path === '/auth/capabilities') return reply({ enabled: false });
    if (path === '/users/me/model-preferences') {
      if (method === 'PUT') { if (body.config_id) modelPreferences.set(body.context_key, body.config_id); else modelPreferences.delete(body.context_key); }
      return reply({ items: [...modelPreferences].map(([context_key, config_id]) => ({ context_key, config_id })) });
    }
    if (path === '/agent/status') return reply({ enabled: false, schema_ready: false });
    if (path === `${root}/agent-artifacts`) return reply(paged([]));
    if (path === '/native-voice/capabilities' || path === '/ai/generation-batches/capabilities') return reply({ enabled: ui });
    if (path === '/projects') {
      if (controls.delayProjects) await new Promise(resolve => setTimeout(resolve, controls.delayProjects));
      if (controls.errorProjects) return reply({ error: { code: 'OFFLINE', message: '服务暂时不可用，请重新加载。' } }, 503);
      if (method === 'POST') return reply({ ...project, ...body }, 201);
      return reply(paged(controls.emptyProjects ? [] : projects.filter(p => !query.get('q') || p.name.includes(query.get('q')!))));
    }
    if (path === '/projects/10/sound-mode') return reply({ mode: 'native', row_version: 1 });
    if (/^\/projects\/10\/characters\/\d+\/voice$/.test(path)) return reply({ row_version: 1, record_id: null, candidates: [] });
    if (path.endsWith('/dialogue')) return reply({ row_version: 1, mode: 'native', document: { lines: [{ character_id: '501', text: '这封信，终于送到了。', delivery: '平静而克制', speech: 'onscreen' }], reviewed: true }, characters: [{ id: '501', name: '林晚' }], voices: [] });
    if (path === `${root}/assembly`) {
      if (method === 'PATCH') { assembly.assembly.row_version = String(Number(assembly.assembly.row_version) + 1); Object.assign(assembly.assembly, { resolution: body.resolution }); }
      return reply(assembly);
    }
    if (path === `${root}/assembly/exports`) return reply({ items: [], has_more: false });
    if (path === `${root}/sound/capabilities`) return reply({ enabled: ui });
    if (path === `${root}/sound`) { if (method === 'PUT') Object.assign(sound, { document: body.document, row_version: sound.row_version + 1 }); return reply(sound); }
    if (path === '/ai/generation-batches') return reply(paged([batch]));
    if (path === '/ai/generation-batches/6001') return reply({ ...batch, ...paged([0, 1, 2].map((_, i) => ({ id: String(6101 + i), source_id: String(101 + i), name: `分镜 ${i + 1}`, task_id: '8001', status: 'succeeded', error: null, task: task('8001', 'shot_image') }))) });
    if (path === '/ai/generation-batches/preflight') return reply({ preflight_hash: 'test', task_count: 1, output_count: 1, concurrency: 1, items: [{ source_id: '501', name: '林晚', state: 'ready', reason: '', task_id: null }] });
    if (ui && path === '/ai-model-configs') return reply(paged([model(query.get('service_type') || 'text')]));
    if (ui && path === '/ai-model-configs/77') return reply(model('text'));
    if (ui && path === '/media-library/items') return reply(paged(media.map(m => query.get('media_type') === 'video' ? { ...m, media_type: 'video', url: null, duration_ms: 3000 } : m)));
    if (ui && /^\/media-library\/items\/\d+$/.test(path)) return reply(media.find(m => m.asset_id === path.split('/').pop()) || media[0]);
    if (ui && path === '/ai/generations' && !query.get('source_scene')) return reply(paged(['succeeded', 'running', 'failed', 'cancelled'].filter(s => !query.get('status') || query.get('status') === s).map((status, i) => ({ ...task(String(8001 + i), 'generic'), service_type: query.get('service_type') || 'text', status, config: { id: '77', name: '本地验收模型', model_key: 'fixture', provider: '测试替身' }, display_context: { project: '雨夜来信', episode: '第 1 集：归来的旅人', subject: ['剧本改编', '画面生成', '参考图参数需核对', '测试任务'][i], scope: '通用任务' }, error: status === 'failed' ? { code: 'FIXTURE', message: '输入参数需要核对，请核对输入参数。' } : null }))));
    if (path === '/projects/10' || path === '/projects/10/open') return reply(project);
    if (path === '/projects/10/episodes') return reply(paged([episode]));
    if (path === root) { if (method === 'PATCH') Object.assign(episode, body); return reply(episode); }
    if (path === `${root}/writing`) return reply(writing);
    if (path === `${root}/novel`) { writing.novel.content = body.content; writing.content_version = String(BigInt(writing.content_version) + 1n); return reply({ content_version: writing.content_version, novel: writing.novel }); }
    if (path === `${root}/script`) { writing.editing_script.content = body.content; writing.editing_script.state = 'unconfirmed'; writing.confirmed_script_id = null; writing.content_version = String(BigInt(writing.content_version) + 1n); return reply({ content_version: writing.content_version, script: writing.editing_script }); }
    if (path === `${root}/scripts`) return reply(paged([candidate]));
    if (path === `${root}/scripts/41`) return reply(candidate);
    if (path === `${root}/editing-script`) { writing.editing_script = { ...writing.editing_script, id: '41', content: candidate.content, state: 'unconfirmed' }; writing.confirmed_script_id = null; writing.content_version = String(BigInt(writing.content_version) + 1n); return reply(writing); }
    if (/\/scripts\/\d+\/confirm$/.test(path)) { writing.editing_script.state = 'confirmed'; writing.confirmed_script_id = writing.editing_script.id; return reply(writing); }
    if (path === '/ai-model-configs') return reply(paged([{ id: '77', name: '测试替身模型', model_key: 'fixture', service_type: query.get('service_type'), provider: 'fixture', enabled: 1, is_default: 1, is_deleted: 0, row_version: '1', has_api_key: true }]));
    if (path.endsWith('/capabilities')) return reply({ known: true, reference_images: true, parameters: ['aspect', 'resolution', 'count'] });
    if (path === `${root}/assets` || path === '/projects/10/assets' || path === '/libraries/global/assets') {
      if (method === 'POST') { const next = { ...asset, ...body, id: '502', name: body.name }; assets.push(next); return reply(next, 201); }
      return reply(paged(assets.filter(item => !query.get('kind') || item.kind === query.get('kind'))));
    }
    if (path === '/assets/501') { if (method === 'PATCH') Object.assign(asset, body, { row_version: String(Number(asset.row_version) + 1) }); return reply(asset); }
    if (path === '/assets/501/image-candidates') return reply(paged([]));
    if (path.startsWith('/generation-references/')) {
      const [, , kind, id, mediaId] = path.split('/');
      const key = `${kind}/${id}`;
      const owner = kind === 'asset' ? asset : shots.find(shot => shot.id === id)!;
      referenceState[key] ??= [];
      if (method !== 'GET') {
        owner.row_version = String(Number(owner.row_version) + 1);
        if (kind === 'shot') { (owner as typeof shots[number]).context_hash = 'b'.repeat(64); storyboardVersion = String(Number(storyboardVersion) + 1); }
        if (method === 'POST') referenceState[key].push({ media_id: '991', name: 'reference.png', url: image });
        else referenceState[key] = referenceState[key].filter(item => item.media_id !== mediaId);
      }
      return reply({ row_version: owner.row_version, items: referenceState[key] });
    }
    if (path === `${root}/shots`) return reply({ ...paged(shots), episode_id: '20', storyboard_version: storyboardVersion });
    if (new RegExp(`^${root}/shots/\\d+$`).test(path)) {
      const shot = shots.find(item => item.id === path.split('/').pop())!;
      if (method === 'PATCH') { Object.assign(shot, body, { row_version: String(Number(shot.row_version) + 1) }); storyboardVersion = String(Number(storyboardVersion) + 1); }
      return reply({ shot, storyboard_version: storyboardVersion });
    }
    if (path.includes('/storyboard-results/') && path.endsWith('/shots')) return reply({ ...paged(Array.from({ length: 55 }, (_, index) => ({ position: index + 1, title: `站台镜头 ${index + 1}`, script: `历史分镜 ${index + 1}：列车驶过雨夜。`, duration_ms: 3000, asset_ids: ['501'], assets: [{ id: '501', kind: 'character', name: '林晚', available: true, snapshot_missing: false }], source_excerpt: '林晚走进车站，抬头寻找站台。', story_beat: '寻找信件主人' }))), generation_id: '8001', total_duration_ms: 165000, applied: null });
    if (path.endsWith('/apply')) { storyboardVersion = String(Number(storyboardVersion) + 1); return reply({ generation_id: '8001', mode: body.mode, storyboard_version: storyboardVersion, shot_ids: [], already_applied: false }); }
    if (path === '/ai/generations') {
      const scene = query.get('source_scene');
      return reply(paged(scene === 'novel_script' ? [task('77001', 'novel_script')] : scene === 'script_assets' ? [task('88001', 'script_assets')] : query.get('service_type') === 'image' ? [task('92001', 'asset_image')] : textTasks));
    }
    if (path === '/ai/generations/image' || path === '/ai/generations/text') return reply({ generation_id: '99001', service_type: path.endsWith('image') ? 'image' : 'text', status: 'queued' }, 202);
    if (path.endsWith('/records')) return reply(paged([]));
    if (/^\/ai\/generations\/\d+$/.test(path)) { const id = path.split('/').pop()!; return reply({ ...task(id, id === '88001' ? 'script_assets' : 'asset_image'), input: { prompt: '测试生成提示词', reference_media_ids: ['991'], secret_test_field: 'DO_NOT_RENDER' }, effective_prompt: '测试生成提示词', parameters: { count: 1, secret_parameter: 'DO_NOT_RENDER' }, result: { text: null, assets: [], partial: false } }); }
    if (path === `${root}/asset-extraction-results/88001`) return reply({ generation_id: '88001', result_version: '1', content_version: writing.content_version, stale: false, kinds: ['character'], items: [{ candidate_id: 'a'.repeat(32), original: { ...asset, aliases: [], importance: 'core', story_function: '寻找信件主人' }, draft: { ...asset, prompt: '隐藏的人物图片提示词' }, matches: [], duplicate_candidates: [], applied: null }] });
    if (path === '/media-library/items') return reply(paged([]));
    unexpected.push(`${method} ${path}`); return reply({ error: { code: 'UNEXPECTED_FIXTURE', message: path } }, 501);
  });
  return { requests, unexpected, errors, writing, shots, asset, referenceState, controls };
}
