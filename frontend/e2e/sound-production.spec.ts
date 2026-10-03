import { test, expect } from '@playwright/test';
import { writeFileSync } from 'node:fs';
test('reviewed dialogue, lost speech receipt, explicit adoption and SRT/music edits', async ({ page }) => {
  const sound = '/api/v1/projects/1/episodes/2/sound';
  let state: any = { row_version: 1, timeline_hash: 'a'.repeat(64), duration_ms: 3000, needs_review: true, stale_lines: [], uploads: [], media: {}, voice_defaults: { row_version: 0, voices: {} }, document: { dialogue: [{ id: 'line1', character: '甲', text: '你好，世界。', voice: 'voice1', config_id: '77', start_ms: 500, media_id: null, adopted_hash: null }], subtitles: [], music: null, original_volume: 1, dialogue_volume: 1, burn_subtitles: false, font_size: 24 } };
  const keys: string[] = [], bodies: any[] = [], saved: any[] = [], adoption: any[] = [];
  let generated = false;
  const nativeDialogs: string[] = [];
  page.on('dialog', dialog => { nativeDialogs.push(dialog.message()); void dialog.dismiss(); });
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), url = new URL(req.url());
    const body = req.method() === 'GET' ? null : req.postDataJSON();
    const reply = (json: unknown) => route.fulfill({ json });
    if (url.pathname === `${sound}/capabilities`) return reply({ enabled: true });
    if (url.pathname === sound && req.method() === 'GET') return reply(state);
    if (url.pathname === sound) { saved.push(body); state = { ...state, document: body.document, row_version: state.row_version + 1, needs_review: !body.reviewed }; return reply(state); }
    if (url.pathname.endsWith('/ai-model-configs')) return reply({ items: [{ id: '77', name: '配音验收模型', model_key: 'fixture', provider: 'fixture', service_type: url.searchParams.get('service_type'), enabled: 1, is_default: 1, row_version: '1' }], total: 1, offset: 0, limit: 100 });
    if (url.pathname === `${sound}/candidates/line1`) return reply(generated ? [{ id: '900', status: 'succeeded', can_resume: false, line_hash: 'b'.repeat(64), outputs: [{ media_id: '901', duration_ms: 500, url: '/.runtime/timeline-fixture.mp4' }] }] : []);
    if (url.pathname === '/api/v1/ai/generations/audio') { keys.push(req.headers()['idempotency-key']); bodies.push(body); if (keys.length === 1) return route.abort('failed'); generated = true; return reply({ generation_id: '900', service_type: 'audio', status: 'queued' }); }
    if (url.pathname === `${sound}/adopt`) { adoption.push(body); state.document.dialogue[0].media_id = '901'; state.document.dialogue[0].adopted_hash = 'b'.repeat(64); state.media['901'] = { duration_ms: 500, url: '/.runtime/timeline-fixture.mp4' }; state.row_version++; return reply(state); }
    if (url.pathname === `${sound}/subtitles/import`) return reply({ subtitles: [{ start_ms: 500, end_ms: 1000, text: '你好，世界。' }] });
    throw new Error(`Unexpected sound endpoint ${req.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/sound-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('button', { name: /1. 甲/ }).click();
  await page.getByRole('button', { name: '生成此条配音' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '无法连接服务' })).toBeVisible();
  await page.getByRole('button', { name: '生成此条配音' }).click();
  await expect(page.getByRole('button', { name: '采用此配音' })).toBeVisible();
  expect(keys[0]).toBe(keys[1]); expect(bodies[0]).toEqual(bodies[1]); expect(adoption).toEqual([]);
  await page.getByRole('button', { name: '采用此配音' }).click();
  await expect(page.getByRole('button', { name: '取消采用', exact: true })).toBeVisible();
  expect(adoption[0]).toEqual({ row_version: 1, line_id: 'line1', media_id: '901' });
  await page.getByRole('tab', { name: /字幕/ }).click();
  await page.locator('input[accept=".srt"]').setInputFiles({ name: 'test.srt', mimeType: 'application/x-subrip', buffer: Buffer.from('1\n00:00:00,500 --> 00:00:01,000\n你好，世界。\n') });
  await expect(page.getByLabel('字幕文本')).toHaveValue('你好，世界。');
  await page.getByRole('checkbox', { name: '导出时烧录字幕' }).check();
  await page.getByRole('checkbox', { name: '已核对当前剪辑的声音与字幕时间' }).check();
  await page.getByRole('button', { name: '保存声音草稿' }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].reviewed).toBe(true); expect(saved[0].document.burn_subtitles).toBe(true);
  expect(saved[0].document.dialogue[0].media_id).toBe('901');
  await page.getByRole('tab', { name: '配乐与混音' }).click();
  await page.getByLabel('原视频声音音量', { exact: true }).fill('0.5');
  await page.getByRole('button', { name: '保存声音草稿' }).click();
  await expect.poll(() => saved.length).toBe(2);
  expect(saved[1].document.original_volume).toBe(0.5); expect(saved[1].reviewed).toBe(false);
  await page.screenshot({ path: '.runtime/sound-desktop.png', animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => {
    const box = await page.getByRole('dialog', { name: '配音、字幕和配乐', exact: true }).evaluate(node => ({
      rect: node.getBoundingClientRect().toJSON(),
      wrapperMaxWidth: getComputedStyle(node.closest('.ant-drawer-content-wrapper')!).maxWidth,
      viewportWidth: innerWidth,
      visualViewportWidth: visualViewport?.width,
      documentWidth: document.documentElement.clientWidth,
      devicePixelRatio,
    }));
    writeFileSync('.runtime/sound-mobile-geometry.json', JSON.stringify(box, null, 2));
    const viewportWidth = box.visualViewportWidth ?? box.viewportWidth;
    return box.rect.x >= -1 && box.rect.width <= viewportWidth && box.rect.right <= viewportWidth;
  }).toBe(true);
  await page.screenshot({ path: '.runtime/sound-mobile.png', animations: 'disabled' });
  await page.getByRole('checkbox', { name: '已核对当前剪辑的声音与字幕时间' }).check();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改' }).click();
  await expect(page.getByRole('dialog', { name: '配音、字幕和配乐', exact: true })).toBeHidden();
  await page.getByRole('button', { name: '检查未保存状态' }).click();
  await expect(page.getByLabel('未保存状态')).toHaveText('false');
  expect(saved.length).toBe(2);
  expect(errors).toEqual([]);
  expect(nativeDialogs).toEqual([]);
});

test('read-only episode does not open an editable sound panel', async ({ page }) => {
  await page.route('**/sound/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.goto('/e2e/sound-fixture.html?readonly=1');
  await expect(page.getByRole('button', { name: '声音、字幕和配乐', exact: true })).toBeDisabled();
});
