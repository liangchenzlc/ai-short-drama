import { test, expect } from '@playwright/test';

test('shared adopted character voice remains playable when the collaborator has no private candidates', async ({ page }) => {
  const current = { media_id: '90', url: '/.runtime/timeline-fixture.mp4?voice=1', duration_ms: 4000, row_version: 2 };
  let reads = 0;
  const unexpected: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const reply = (json: unknown) => route.fulfill({ json });
    if (path.endsWith('/native-voice/capabilities')) return reply({ enabled: true });
    if (path.endsWith('/ai-model-configs')) return reply({ items: [], total: 0 });
    if (path.endsWith('/voice')) { reads++; return reply({ row_version: 2, record_id: '9', candidates: [], current_voice: current }); }
    if (path.endsWith('/dialogue')) return reply({ row_version: 1, mode: 'native', document: { reviewed: true, lines: [] }, characters: [], voices: [] });
    unexpected.push(path); return route.fulfill({ status: 501, json: {} });
  });
  await page.goto('/e2e/native-voice-fixture.html');
  const voice = page.getByRole('region', { name: '当前采用音色', exact: true });
  await expect(voice).toBeVisible();
  await expect(page.getByText('还没有音色候选。填写描述，生成后试听选择。', { exact: true })).toBeVisible();
  const audio = voice.locator('audio');
  await audio.evaluate(async node => { node.muted = true; await node.play(); });
  await expect.poll(() => audio.evaluate(node => !node.paused && node.readyState >= 2)).toBe(true);
  await audio.evaluate(node => node.pause());
  const before = reads;
  current.url = '/.runtime/timeline-fixture.mp4?voice=2';
  await page.getByRole('button', { name: '刷新声音候选', exact: true }).click();
  await expect.poll(() => reads).toBe(before + 1);
  await expect(audio).toHaveAttribute('src', current.url);
  await expect(page.getByRole('button', { name: '设为角色音色', exact: true })).toHaveCount(0);
  expect(unexpected).toEqual([]); expect(errors).toEqual([]);
});

test('character sample design replay, explicit binding and reviewed two-speaker dialogue', async ({ page }, info) => {
  const keys: string[] = [], requests: any[] = [], adopted: any[] = [], saved: any[] = [];
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  let voices: any = { row_version: 0, record_id: null, candidates: [] };
  let dialogue: any = { row_version: 0, mode: 'native', document: { reviewed: false, lines: [] }, characters: [{ id: '3', name: '林晚' }, { id: '5', name: '陈舟' }], voices: [] };
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    const body = req.method() === 'GET' ? null : req.postDataJSON();
    const reply = (json: unknown) => route.fulfill({ json });
    if (path.endsWith('/native-voice/capabilities')) return reply({ enabled: true });
    if (path.endsWith('/ai-model-configs')) return reply({ items: [{ id: '77', name: '百炼 CosyVoice', model_key: 'cosyvoice-v3.5-flash', provider: '百炼', service_type: 'audio', enabled: 1, is_default: 1, row_version: '1' }], total: 1 });
    if (path.endsWith('/voice')) return reply(voices);
    if (path.endsWith('/ai/generations/audio')) {
      keys.push(req.headers()['idempotency-key']); requests.push(body);
      if (keys.length === 1) return route.abort('failed');
      voices.candidates = [{ record_id: '9', task_id: '8', status: 'succeeded', adoptable: true, can_resume: false, duration_ms: 4000, description: body.source.voice_prompt, preview_text: body.source.preview_text, url: '/.runtime/timeline-fixture.mp4', voice_id: 'fixture' }];
      return reply({ generation_id: '8' });
    }
    if (path.endsWith('/voice/adopt')) { adopted.push(body); voices = { ...voices, row_version: 1, record_id: '9' }; return reply(voices); }
    if (path.endsWith('/dialogue') && req.method() === 'GET') return reply(dialogue);
    if (path.endsWith('/dialogue')) { saved.push(body); dialogue = { ...dialogue, document: body.document, row_version: dialogue.row_version + 1 }; return reply(dialogue); }
    throw new Error(`Unexpected endpoint ${path}`);
  });
  await page.goto('/e2e/native-voice-fixture.html');
  await page.getByLabel('声音描述', { exact: true }).fill('青年男性，中低音，语气平稳自然');
  await page.getByRole('button', { name: '设计一个音色候选' }).click();
  await expect(page.getByRole('alert')).toContainText('无法连接服务');
  await page.getByRole('button', { name: '设计一个音色候选' }).click();
  await expect(page.getByRole('button', { name: '设为角色音色' })).toBeVisible();
  expect(keys[0]).toBe(keys[1]); expect(requests[0]).toEqual(requests[1]); expect(adopted).toEqual([]);
  expect(requests[0].source.scene).toBe('character_voice_design');
  await page.getByRole('button', { name: '设为角色音色' }).click();
  await expect(page.getByText('当前角色音色', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('character-voice.png'), animations: 'disabled' });
  expect(adopted[0]).toEqual({ row_version: 0, record_id: '9' });
  await page.getByRole('button', { name: '编辑并确认对白' }).click();
  await page.getByRole('button', { name: '添加台词' }).click();
  await page.getByLabel('第1句台词').fill('今天一起出发吧。');
  await page.getByRole('button', { name: '添加台词' }).click();
  await page.getByRole('combobox', { name: '第2句角色' }).press('ArrowDown');
  await page.getByText('陈舟', { exact: true }).click();
  await page.getByLabel('第2句台词').fill('好，我已经准备好了。');
  await page.getByRole('checkbox', { name: '已确认对白顺序与角色；空列表表示无对白' }).check();
  await page.screenshot({ path: '.runtime/native-desktop.png', animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => { const box = await page.getByRole('dialog').boundingBox(); return !!box && box.x >= -1 && box.width <= 390; }).toBe(true);
  await page.screenshot({ path: '.runtime/native-mobile.png', animations: 'disabled' });
  await page.getByRole('button', { name: '保存分镜对白' }).click();
  await expect(page.getByRole('dialog')).toBeHidden();
  expect(saved[0].document.reviewed).toBe(true);
  expect(saved[0].document.lines.map((l: any) => l.character_id)).toEqual(['3', '5']);
  expect(requests.length).toBe(2); expect(errors).toEqual([]);
});

test('native finishing exposes original audio, subtitles and music without per-shot dubbing', async ({ page }) => {
  const saved: any[] = [];
  let state: any = { mode: 'native', row_version: 1, timeline_hash: 'a'.repeat(64), duration_ms: 4000, needs_review: false, stale_lines: [], uploads: [], media: {}, voice_defaults: { row_version: 0, voices: {} }, document: { dialogue: [], subtitles: [], native_ducking: [], music: null, original_volume: 1, dialogue_volume: 1, burn_subtitles: false, font_size: 24 } };
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (path.endsWith('/native-subtitles')) return route.fulfill({ json: { timeline_hash: state.timeline_hash, subtitles: [{ start_ms: 0, end_ms: 4000, text: '视频中的台词。' }], reviewed: false } });
    if (req.method() === 'PUT') { const body = req.postDataJSON(); saved.push(body); state = { ...state, document: body.document, row_version: 2 }; }
    return route.fulfill({ json: state });
  });
  await page.goto('/e2e/sound-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await expect(page.getByRole('tab', { name: /配音/ })).toHaveCount(0);
  await page.getByRole('button', { name: '从采用视频的台词创建字幕草稿' }).click();
  await expect(page.getByLabel('字幕文本')).toHaveValue('视频中的台词。');
  await expect(page.getByRole('checkbox', { name: '已核对当前剪辑的声音与字幕时间' })).not.toBeChecked();
  await page.getByRole('button', { name: '用已校对字幕设置对白压低区间' }).click();
  await page.getByRole('tab', { name: '配乐与混音' }).click();
  await expect(page.getByText('配音音量', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '保存声音草稿' }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].document.dialogue).toEqual([]);
  expect(saved[0].document.native_ducking).toHaveLength(1);
});
