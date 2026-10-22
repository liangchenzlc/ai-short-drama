import { test, expect, type Page } from '@playwright/test';
import type { AssemblyState, AssemblyMaterial, MediaJob } from '../../src/api/modules/assembly';
import { emptySound } from '../../src/features/projects/assembly-document';

const root = '/api/v1/projects/10/assembly';
async function fixture(page: Page, configure?: (state: AssemblyState) => void) {
  const source = { id: '1001', shot_id: '101', source_type: 'shot' as const, media_id: '201', position: 1, shot_position: 1, script: '开场画面', included: true, muted: false, trim_in_ms: 0, trim_out_ms: 2000, duration_ms: 3000, display_duration_ms: null, width: 640, height: 360, has_audio: true, url: '/.runtime/timeline-fixture.mp4', poster: '/.runtime/timeline-poster.jpg', filmstrip: { url: '/.runtime/timeline-filmstrip.jpg', count: 3, interval_ms: 1000 }, is_stale: false, archived: false, issue: null };
  const materials: AssemblyMaterial[] = [
    { media_id: '202', kind: 'image', name: '片尾图片', url: '/.runtime/timeline-poster.jpg', poster: '/.runtime/timeline-poster.jpg', width: 640, height: 360, duration_ms: null, has_audio: false },
    { media_id: '203', kind: 'video', name: '较短视频', url: source.url, poster: source.poster, width: 640, height: 360, duration_ms: 1000, has_audio: true },
    { media_id: '204', kind: 'audio', name: '背景配乐', url: source.url, poster: null, width: null, height: null, duration_ms: 3000, has_audio: false },
  ];
  const doc = emptySound(); doc.burn_subtitles = true; doc.subtitles = [{ id: 'caption', text: '原字幕', start_ms: 0, end_ms: 1000, anchor_clip_id: '1001', anchor_source_in_ms: 0 }];
  doc.audio_clips = [{ id: 'voice', media_id: '201', name: '开场原声', kind: 'original', start_ms: 0, duration_ms: 2000, trim_in_ms: 0, trim_out_ms: 2000, volume: 1, fade_in_ms: 0, fade_out_ms: 0, loop: false, ducking: false, muted: false, gain_db: 0, anchor_clip_id: '1001', anchor_source_in_ms: 0 }];
  const state: AssemblyState = { editor_version: 2, assembly: { id: '100', row_version: '1', aspect: '16:9', resolution: '720p', current_media_id: null }, clips: [source], sources: [source], source_hash: 'a'.repeat(64), context_hash: 'b'.repeat(64), voice_context_hash: 'voice-1', materials, sound: { row_version: '1', timeline_hash: 'c'.repeat(64), duration_ms: 2000, needs_review: false, stale_lines: [], document: doc, media: { '201': { url: source.url, duration_ms: 3000 }, '204': { url: source.url, duration_ms: 3000 } }, uploads: [], voice_defaults: { row_version: '0', voices: {} } }, jobs: [], subtitle_font: 'Microsoft YaHei' };
  const saves: any[] = [], jobs: MediaJob[] = [], errors: string[] = [], controls = { failSave: false, changedVoice: false, analysisFail: false };
  configure?.(state);
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    const body = request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path.endsWith('/sound/capabilities')) return reply({ enabled: false });
    if (path === `${root}/editor`) {
      if (method === 'PUT') {
        saves.push(body); if (controls.failSave) return reply({ error: { code: 'conflict', message: '版本已变化' } }, 409);
        state.clips = body.clips.map((clip: any, index: number) => {
          const material = materials.find(m => m.media_id === clip.media_id);
          return { ...(state.clips?.find(c => c.id === clip.id) ?? source), ...clip, position: index + 1, shot_id: clip.source_type === 'shot' ? '101' : null, duration_ms: clip.source_type === 'image' ? clip.display_duration_ms : material?.duration_ms ?? 3000, script: material?.name ?? source.script, url: material?.url ?? source.url, poster: material?.poster ?? source.poster };
        });
        state.sound!.document = body.sound; state.sound!.row_version = String(BigInt(state.sound!.row_version) + 1n); state.assembly!.row_version = String(BigInt(state.assembly!.row_version) + 1n);
      }
      return reply({ ...state, voice_context_hash: controls.changedVoice ? 'voice-changed' : state.voice_context_hash });
    }
    if (path === `${root}/initialize`) return reply(state);
    if (path === `${root}/materials`) return reply({ items: materials });
    if (path === `${root}/materials/import`) return reply(materials.find(m => m.media_id === body.media_id));
    if (path === `${root}/transcription-models`) return reply({ local_available: true, models: [{ id: '77', name: '可选云端模型' }] });
    if (path === `${root}/media-jobs`) return reply({ items: jobs });
    if (path === `${root}/audio-analysis`) {
      if (controls.analysisFail) return reply({ error: { code: 'unavailable', message: '分析服务暂不可用' } }, 503);
      return reply({ id: '900', kind: 'audio_analysis', status: 'succeeded', stage: 'complete', progress: 100, can_cancel: false, can_retry: false, error: null, result: { waveform: [0, .2, .4, .3, .1], gain_db: 2 } });
    }
    if (path === `${root}/transcriptions`) {
      const job: MediaJob = { id: '901', kind: 'transcription', status: 'succeeded', stage: 'complete', progress: 100, can_cancel: false, can_retry: false, error: null, result: { start_ms: 0, end_ms: 2000, voice_context_hash: 'voice-1', subtitles: [{ id: 'recognized', start_ms: 0, end_ms: 1500, text: '识别结果' }] } }; jobs.unshift(job); return reply(job, 202);
    }
    if (path === `${root}/media-jobs/901`) return reply(jobs[0]);
    if (path.endsWith('/ai-model-configs')) return reply({ items: [], total: 0, offset: 0, limit: 100 });
    return reply({ error: { code: 'unexpected_fixture_request', message: `${method} ${path}` } }, 404);
  });
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html?inline=1');
  await expect(page.getByRole('region', { name: '音画分轨时间轴' })).toBeVisible();
  return { state, saves, errors, controls };
}

test('multitrack autosave, picture association, undo and real preview clock', async ({ page }) => {
  const data = await fixture(page);
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  await expect.poll(() => page.getByLabel('播放位置').innerText()).not.toContain('00:00:00 /');
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await page.getByRole('spinbutton', { name: '定位时间（秒）' }).fill('1');
  await page.getByRole('button', { name: '分割', exact: true }).click();
  await expect.poll(() => data.state.clips?.length).toBe(2);
  expect(data.state.sound!.document.audio_clips).toHaveLength(2);
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(() => data.state.clips?.length).toBe(1);
  expect(data.state.sound!.document.audio_clips).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

async function recordPreviewAudio(page: Page) {
  await page.addInitScript(() => {
    const NativeAudio = window.Audio;
    const elements: HTMLAudioElement[] = [];
    Object.assign(window, { previewAudio: elements });
    window.Audio = function (src?: string) { const element = new NativeAudio(src); elements.push(element); return element; } as typeof Audio;
  });
}

test('explicit media refresh rebuilds audio with the new URL and retains the playhead', async ({ page }) => {
  await recordPreviewAudio(page);
  const data = await fixture(page);
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await page.getByRole('spinbutton', { name: '定位时间（秒）' }).fill('0.5');
  await expect(page.getByLabel('播放位置')).toContainText('00:00:15');
  await page.evaluate(() => {
    const elements = (window as unknown as { previewAudio: HTMLAudioElement[] }).previewAudio;
    elements.at(-1)!.play = () => Promise.reject(new Error('音频地址已过期'));
  });
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '刷新媒体并重试', exact: true })).toBeVisible();
  const fresh = '/.runtime/timeline-fixture.mp4?fresh-audio=1';
  data.state.sound!.media['201'].url = fresh;
  data.state.clips![0].url = fresh;
  await page.getByRole('button', { name: '刷新媒体并重试', exact: true }).click();
  await expect(page.getByRole('button', { name: '刷新媒体并重试', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('播放位置')).toContainText('00:00:15');
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  expect(await page.evaluate(() => {
    const elements = (window as unknown as { previewAudio: HTMLAudioElement[] }).previewAudio;
    return { count: elements.length, oldSrc: elements[0].getAttribute('src'), currentSrc: elements.at(-1)!.src };
  })).toEqual({ count: 2, oldSrc: null, currentSrc: `http://127.0.0.1:4175${fresh}` });
  expect(data.errors).toEqual([]);
});

test('polling signed media URLs keeps active audio and playback uninterrupted', async ({ page }) => {
  await recordPreviewAudio(page);
  const data = await fixture(page, state => {
    Object.assign(state.clips![0], { url: '/.runtime/timeline-long-fixture.mp4', trim_out_ms: 10000, duration_ms: 12000 });
    Object.assign(state.sound!.document.audio_clips![0], { duration_ms: 10000, trim_out_ms: 10000 });
    Object.assign(state.sound!.media['201'], { url: '/.runtime/timeline-long-fixture.mp4', duration_ms: 12000 });
    state.sound!.duration_ms = 10000;
  });
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  data.state.clips![0].script = '地址续签后的画面';
  data.state.clips![0].url = '/.runtime/timeline-long-fixture.mp4?renewed=1';
  data.state.sound!.media['201'].url = '/.runtime/timeline-long-fixture.mp4?renewed=1';
  await expect(page.getByRole('button', { name: '画面 地址续签后的画面', exact: true })).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  expect(await page.evaluate(() => {
    const elements = (window as unknown as { previewAudio: HTMLAudioElement[] }).previewAudio;
    return { count: elements.length, src: elements[0].src, paused: elements[0].paused };
  })).toEqual({ count: 1, src: 'http://127.0.0.1:4175/.runtime/timeline-long-fixture.mp4', paused: false });
  expect(data.errors).toEqual([]);
});

test('materials, shorter replacement confirmation, image duration and compact inspector', async ({ page }, info) => {
  const data = await fixture(page);
  await page.getByRole('button', { name: '展开编辑设置', exact: true }).click();
  await page.getByRole('button', { name: '替换素材', exact: true }).click();
  await page.getByRole('button', { name: '替换为 较短视频', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '替换素材较短', exact: true })).toBeVisible();
  expect(data.state.clips![0].media_id).toBe('201');
  await page.getByRole('button', { name: '保留原片段', exact: true }).click();
  await page.getByRole('button', { name: '添加画面素材', exact: true }).click();
  await page.locator('.ant-segmented-item-label').filter({ hasText: /^图片$/ }).click();
  await page.getByRole('button', { name: '添加 片尾图片', exact: true }).click();
  await expect.poll(() => data.state.clips?.length).toBe(2);
  expect(data.state.clips![1].source_type).toBe('image');
  await page.getByLabel('图片展示时长', { exact: true }).fill('3');
  await expect.poll(() => data.state.clips![1].display_duration_ms).toBe(3000);
  await page.getByRole('button', { name: '添加声音素材', exact: true }).click();
  await page.getByRole('button', { name: '添加 背景配乐', exact: true }).click();
  await expect.poll(() => data.state.sound!.document.audio_clips!.length).toBe(2);
  const music = data.state.sound!.document.audio_clips!.find(c => c.kind === 'music')!;
  expect(music.anchor_clip_id).toBeFalsy();
  await page.getByRole('button', { name: '统一响度', exact: true }).click();
  await expect.poll(() => data.state.sound!.document.audio_clips!.find(c => c.kind === 'music')!.gain_db).toBe(2);
  await page.screenshot({ path: info.outputPath('multitrack-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('dialog', { name: '成片编辑设置', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath('multitrack-mobile.png'), fullPage: true, animations: 'disabled' });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(data.errors).toEqual([]);
});

test('recognition is previewed before applying and stale voice inputs cannot overwrite captions', async ({ page }) => {
  const data = await fixture(page);
  await page.getByRole('button', { name: '语音识别字幕', exact: true }).click();
  await page.getByRole('button', { name: '开始识别', exact: true }).click();
  await expect(page.locator('.assembly-transcript-results')).toContainText('识别结果');
  expect(data.state.sound!.document.subtitles[0].text).toBe('原字幕');
  data.controls.changedVoice = true;
  await page.getByRole('button', { name: '应用字幕', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '人声音轨已变化' })).toBeVisible();
  expect(data.state.sound!.document.subtitles[0].text).toBe('原字幕');
  data.controls.changedVoice = false;
  await page.getByRole('button', { name: '应用字幕', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect.poll(() => data.state.sound!.document.subtitles[0].text).toBe('识别结果');
  expect(data.errors).toEqual([]);
});

test('locked tracks protect edits, dragged materials insert and caption input saves before blur', async ({ page }) => {
  const data = await fixture(page);
  await page.getByRole('button', { name: '锁定画面轨', exact: true }).click();
  await expect(page.getByRole('button', { name: '删除片段', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '构图拖动', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '解锁画面轨', exact: true }).click();
  await page.getByRole('button', { name: '画面 开场画面', exact: true }).click();
  await page.getByRole('article').filter({ hasText: '片尾图片' }).dragTo(page.locator('.assembly-multitrack-canvas'), { targetPosition: { x: 600, y: 60 } });
  await expect.poll(() => data.state.clips?.length).toBe(2);
  expect(data.saves.at(-1).clips[1].source_clip_id).toBeUndefined();
  await page.getByRole('button', { name: '字幕 原字幕', exact: true }).click();
  await page.getByRole('textbox', { name: '校对字幕', exact: true }).fill('即时保存的校对');
  await expect.poll(() => data.state.sound!.document.subtitles[0].text).toBe('即时保存的校对');
  expect(data.errors).toEqual([]);
});
