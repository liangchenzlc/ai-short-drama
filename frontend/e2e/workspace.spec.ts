import { test, expect, type Page } from '@playwright/test';

const root = '/projects/10/episodes/20';
const time = '2026-09-24T00:00:00Z';
const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';

async function fixture(page: Page) {
  const requests: { path: string; method: string; query: URLSearchParams; body: any }[] = [];
  const unexpected: string[] = [];
  const errors: string[] = [];
  const project = { id: '10', name: '雨夜来信', synopsis: '一封迟到的信改变了两个人的命运。', style: '电影质感', aspect: '16:9', episode_count: 1 };
  const episode = { id: '20', project_id: '10', position: 1, episode_number: 1, title: '归来的旅人', synopsis: '林晚走进雨夜。', style: '电影质感', aspect: '16:9' };
  const writing = { episode_id: '20', content_version: '1', novel: { id: '30', content: '雨夜，林晚拿着一封旧信走入车站。', updated_at: time }, editing_script: { id: '40', content: '林晚走进车站，抬头寻找站台。', state: 'confirmed', updated_at: time }, confirmed_script_id: '40' as string | null };
  const asset = { id: '501', kind: 'character', name: '林晚', label: '主角', description: '黑发，穿着深色风衣。', prompt: '人物肖像，深色风衣', tags: [], scene_time: '', state: 'unconfirmed', row_version: '1', media_id: null, image: null, reference_count: 1, link_id: '601', position: 1 };
  const assets = [asset];
  const shots = Array.from({ length: 80 }, (_, index) => ({ id: String(101 + index), position: index + 1, script: `林晚走过雨夜中的站台，镜头 ${index + 1}，列车的光线映在她的眼睛里。`, duration_ms: 3000, source_excerpt: '', asset_ids: ['501'], row_version: '1', context_hash: 'a'.repeat(64), image_settings: { aspect: 'inherit', resolution: '2K', layout: 'single' }, image: null, deleted_at: null }));
  let storyboardVersion = '1';
  const referenceState: Record<string, any[]> = {};
  const source = (scene: string) => ({ scene, project_id: '10', episode_id: '20', script_id: '40', content_version: '1' });
  const task = (id: string, scene: string) => ({ generation_id: id, service_type: scene.endsWith('image') ? 'image' : 'text', status: 'succeeded', source: scene === 'asset_image' ? { scene, asset_id: '501', row_version: '1', project_id: '10', episode_id: '20' } : source(scene), config: { id: '77', name: '测试替身模型', model_key: 'fixture', provider: 'fixture' }, created_at: time, can_cancel: false, can_retry: false, can_resume: false, display_context: { project: project.name, episode: '第 1 集：归来的旅人', subject: scene === 'asset_image' ? '角色：林晚' : '分镜脚本', scope: '通用任务' } });
  const textTasks = Array.from({ length: 35 }, (_, index) => task(String(8001 + index), 'script_shots'));
  const candidate = { id: '41', position: 2, state: 'unconfirmed', preview: '候选剧本：林晚在站台发现了信的主人。', content: '候选剧本：林晚在站台发现了信的主人。', is_editing: false, is_confirmed: false, generation_id: '77001', created_at: time, updated_at: time };
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== 'http://127.0.0.1:4175') { unexpected.push(request.url()); return route.abort(); }
    if (!url.pathname.startsWith('/api/v1/')) return route.continue();
    const path = url.pathname.slice('/api/v1'.length);
    const query = url.searchParams;
    const method = request.method();
    const body = request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
    requests.push({ path, method, query, body });
    const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const paged = (items: any[]) => { const offset = Number(query.get('offset') || 0); const limit = Number(query.get('limit') || 20); return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }; };
    if (path === '/projects/10' || path === '/projects/10/open') return reply(project);
    if (path === '/projects/10/episodes') return reply(paged([episode]));
    if (path === root) { if (method === 'PATCH') Object.assign(episode, body); return reply(episode); }
    if (path === `${root}/writing`) return reply(writing);
    if (path === `${root}/novel`) { writing.novel.content = body.content; writing.content_version = String(Number(writing.content_version) + 1); return reply({ content_version: writing.content_version, novel: writing.novel }); }
    if (path === `${root}/script`) { writing.editing_script.content = body.content; writing.editing_script.state = 'unconfirmed'; writing.confirmed_script_id = null; writing.content_version = String(Number(writing.content_version) + 1); return reply({ content_version: writing.content_version, script: writing.editing_script }); }
    if (path === `${root}/scripts`) return reply(paged([candidate]));
    if (path === `${root}/scripts/41`) return reply(candidate);
    if (path === `${root}/editing-script`) { writing.editing_script = { ...writing.editing_script, id: '41', content: candidate.content, state: 'unconfirmed' }; writing.confirmed_script_id = null; writing.content_version = String(Number(writing.content_version) + 1); return reply(writing); }
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
    if (path.includes('/storyboard-results/') && path.endsWith('/shots')) return reply({ ...paged(Array.from({ length: 55 }, (_, index) => ({ position: index + 1, script: `历史分镜 ${index + 1}：列车驶过雨夜。` }))), generation_id: '8001', applied: null });
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
  return { requests, unexpected, errors, writing, shots, asset, referenceState };
}

test('project actions stay inside the episode item', async ({ page }) => {
  const state = await fixture(page); await page.goto('/projects/10');
  const item = page.locator('.episode-item');
  await expect(item.getByRole('button', { name: '编辑分集' })).toBeVisible();
  const outer = await item.boundingBox(); const action = await item.getByRole('button', { name: '删除', exact: true }).boundingBox();
  expect(action!.y + action!.height).toBeLessThanOrEqual(outer!.y + outer!.height);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('merged writing imports TXT, collapses navigation and adopts a historical script', async ({ page }, info) => {
  const state = await fixture(page); await page.goto(`${root}/source`);
  await expect(page.getByRole('tab', { name: '本集小说' })).toBeVisible();
  expect(await page.locator('.episode-stage-link').count()).toBe(3);
  await page.getByRole('button', { name: '收起创作流程' }).click();
  await expect(page.locator('.episode-layout')).toHaveClass(/is-collapsed/);
  await page.locator('input[type=file]').first().setInputFiles({ name: '小说.txt', mimeType: 'text/plain', buffer: Buffer.from('导入的雨夜故事。') });
  await expect(page.locator('#episode-novel')).toHaveValue('导入的雨夜故事。');
  await expect.poll(() => state.writing.novel.content).toBe('导入的雨夜故事。');
  await page.screenshot({ path: info.outputPath('writing-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.getByRole('button', { name: '预览全文' }).click();
  await page.getByRole('button', { name: '采用并编辑' }).click();
  await expect(page.getByRole('tab', { name: '剧本定稿' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.episode-script-textarea')).toHaveValue(/候选剧本/);
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('asset drawers persist input images and expose prompts only on click', async ({ page }, info) => {
  const state = await fixture(page); await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新建角色', exact: true }).first().click();
  await expect(page.locator('.asset-create-drawer')).toBeVisible();
  const bounds = await page.locator('.asset-create-drawer').boundingBox(); expect(bounds!.width).toBeCloseTo(720, 0);
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '编辑素材', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: '生成图片', exact: true })).toBeVisible();
  await expect(page.getByText('本次补充要求', { exact: true })).toHaveCount(0);
  await page.locator('.generation-reference-images input[type=file]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(image.split(',')[1], 'base64') });
  await expect(page.locator('.reference-image-strip figure')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('asset-drawer-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await page.getByRole('button', { name: '编辑素材', exact: true }).first().click();
  await expect(page.locator('.reference-image-strip figure')).toHaveCount(1);
  await page.getByRole('link', { name: '查看任务详情' }).first().click();
  await expect(page.getByText('测试生成提示词', { exact: true })).toBeVisible();
  await expect(page.getByText('DO_NOT_RENDER')).toHaveCount(0);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '查看提取结果' }).click();
  await page.getByRole('button', { name: '提取记录' }).click();
  await page.getByRole('button', { name: '查看', exact: true }).click();
  await expect(page.getByText('原文依据', { exact: false })).toHaveCount(0);
  await expect(page.getByLabel('图片生成提示词', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '查看 林晚 的图片生成提示词' }).click();
  await expect(page.getByLabel('图片生成提示词', { exact: true })).toHaveValue('隐藏的人物图片提示词');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('storyboard and history load pages on scroll and image history stays in a dialog', async ({ page }, info) => {
  const state = await fixture(page); await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-summary')).toHaveCount(20);
  // StrictMode can repeat the first request; it must never eagerly fetch later pages.
  expect(state.requests.filter(item => item.path === `${root}/shots`).every(item => Number(item.query.get('offset') || 0) === 0 && item.query.get('limit') === '20')).toBe(true);
  await expect(page.getByRole('button', { name: '归档', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '下载草稿', exact: true })).toHaveCount(0);
  await page.locator('.storyboard-list').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(page.locator('.storyboard-summary')).toHaveCount(40);
  await page.locator('.storyboard-list').evaluate(node => { node.scrollTop = 0; });
  await page.locator('.storyboard-summary').first().click();
  await expect(page.locator('.shot-image-candidates')).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: '关联角色', exact: true })).toBeVisible();
  await page.locator('.generation-reference-images input[type=file]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(image.split(',')[1], 'base64') });
  await expect(page.locator('.reference-image-strip figure')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('storyboard-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect.poll(() => state.requests.filter(item => item.path === '/ai/generations/image').length).toBe(1);
  await expect(page.getByRole('dialog', { name: '分镜图片生成记录' })).toBeVisible();
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await page.getByRole('button', { name: '生成记录', exact: true }).first().click();
  await page.getByRole('button', { name: '查看分镜' }).first().click();
  await expect(page.locator('.compact-shot-list > li')).toHaveCount(20);
  await page.locator('.storyboard-result-scroll').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(page.locator('.compact-shot-list > li')).toHaveCount(40);
  await page.getByRole('button', { name: '追加到现有分镜' }).click();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  expect(state.requests.some(item => item.path.endsWith('/apply') && item.body.mode === 'append')).toBe(true);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('narrow workspaces and drawers fit the viewport', async ({ page }, info) => {
  const state = await fixture(page); await page.setViewportSize({ width: 390, height: 844 });
  for (const stage of ['source', 'storyboard', 'assets']) {
    await page.goto(`${root}/${stage}`);
    await expect(page.getByRole('button', { name: '收起创作流程' })).toBeVisible();
    if (stage === 'storyboard') {
      await expect(page.locator('.storyboard-summary')).toHaveCount(20);
      await page.locator('.lazy-load-more').scrollIntoViewIfNeeded();
      await expect(page.locator('.storyboard-summary')).toHaveCount(40);
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    if (stage === 'assets') await page.getByRole('button', { name: '编辑素材', exact: true }).first().click();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`${stage}-mobile.png`), fullPage: true });
  }
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});
