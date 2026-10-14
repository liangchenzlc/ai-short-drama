import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const secondEpisodeId = '9007199254740993';
const secondEpisodePath = `/projects/10/episodes/${secondEpisodeId}`;

async function episodeSwitchFixture(page: Page, count = 2) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const episodes = Array.from({ length: count }, (_, index) => ({
    id: index === 1 ? secondEpisodeId : String(20 + index),
    project_id: '10', position: index + 1, episode_number: index + 1,
    title: index === 0 ? '归来的旅人' : index === 1 ? '迟到的告白' : `继续前行 ${index + 1}`,
    synopsis: '分集切换测试正文。', aspect: '16:9', style: '电影质感',
  }));
  const controls = { failOffset: null as number | null, overlapNextPage: false };
  const listRequests: { offset: number; limit: number }[] = [];
  await page.route(/\/api\/v1\/projects\/10\/episodes(?:\?.*)?$/, route => {
    const query = new URL(route.request().url()).searchParams;
    const offset = Number(query.get('offset') ?? 0);
    const limit = Number(query.get('limit') ?? 20);
    listRequests.push({ offset, limit });
    if (controls.failOffset === offset) return route.fulfill({
      status: 503, json: { error: { code: 'EPISODE_LIST_OFFLINE', message: '分集服务暂时不可用。' } },
    });
    const items = episodes.slice(offset, offset + limit);
    if (controls.overlapNextPage && offset > 0) items.unshift(episodes[offset - 1]);
    return route.fulfill({ json: { items, total: episodes.length, offset, limit } });
  });
  await page.route(`**/api/v1${secondEpisodePath}/**`, route => {
    const url = new URL(route.request().url());
    const path = url.pathname.slice('/api/v1'.length);
    const emptyPage = { items: [], total: 0,
      offset: Number(url.searchParams.get('offset') ?? 0),
      limit: Number(url.searchParams.get('limit') ?? 20) };
    if (path === `${secondEpisodePath}/assets`) return route.fulfill({ json: emptyPage });
    if (path === `${secondEpisodePath}/shots`) return route.fulfill({ json: {
      ...emptyPage, episode_id: secondEpisodeId, storyboard_version: '1',
    } });
    if (path === `${secondEpisodePath}/assembly`) return route.fulfill({ json: {
      assembly: null, source_hash: 'a'.repeat(64), context_hash: 'a'.repeat(64), source_count: 0,
      clips: [], sources: [], changes: [], jobs: [], current_work: null,
    } });
    if (path === `${secondEpisodePath}/writing`) return route.fulfill({ json: {
      ...state.writing, episode_id: secondEpisodeId,
      novel: { ...state.writing.novel, id: '1300', content: '第二集小说正文。' },
      editing_script: { ...state.writing.editing_script, id: '1400', content: '第二集分集正文。' },
      confirmed_script_id: '1400',
    } });
    if (path === `${secondEpisodePath}/scripts`) return route.fulfill({
      json: { items: [], total: 0, offset: 0, limit: 20 },
    });
    if (path === `${secondEpisodePath}/drafts/${secondEpisodeId}/novel_adaptation`) return route.fulfill({ json: {
      id: '1500', project_id: '10', episode_id: secondEpisodeId, object_id: secondEpisodeId,
      purpose: 'novel_adaptation', row_version: '1', draft_version: '1', source_version: '1',
      model_config_id: '77', input_text: '', image_settings: null, video_settings: null,
      reference_media_ids: [], reference_images: [],
    } });
    return route.fallback();
  });
  await page.route(`**/api/v1${secondEpisodePath}`, route => route.fulfill({ json: episodes[1] }));
  return { ...state, episodes, controls, listRequests };
}

test('selecting the current episode closes the list and a new episode keeps the stage and string ID', async ({ page }) => {
  const data = await episodeSwitchFixture(page);
  await page.goto(`${root}/source`);
  const trigger = page.locator('.episode-switch');
  const list = page.locator('.episode-switch-list');
  await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toBeEditable();
  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  const current = list.getByRole('button', { name: '第 01 集 · 归来的旅人', exact: true });
  await expect(current).toHaveAttribute('aria-current', 'true');
  await current.click();
  await expect(list).toBeHidden();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(page).toHaveURL(url => url.pathname === `${root}/source`);

  await trigger.click();
  await list.getByRole('button', { name: '第 02 集 · 迟到的告白', exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname === `${secondEpisodePath}/source` && !url.search);
  await expect(trigger).toHaveAccessibleName('选择分集：第 2 集 · 迟到的告白');
  await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toContainText('第二集分集正文。');
  const stages = page.getByRole('navigation', { name: '分集制作流程', exact: true });
  await expect(stages.getByRole('button', { name: '素材生成，待准备', exact: true })).toBeVisible();
  await expect(stages.getByRole('button', { name: '分镜制作，待制作', exact: true })).toBeVisible();
  await expect(stages.getByRole('button', { name: '成片合成，待合成', exact: true })).toBeVisible();
  await expect(list).toBeHidden();
  expect(data.listRequests).toEqual([{ offset: 0, limit: 20 }, { offset: 0, limit: 20 }]);
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('an episode list read failure can be retried without leaving the current episode', async ({ page }) => {
  const data = await episodeSwitchFixture(page);
  data.controls.failOffset = 0;
  await page.goto(`${root}/source`);
  await page.locator('.episode-switch').click();
  const list = page.locator('.episode-switch-list');
  await expect(list.getByRole('alert')).toContainText('分集列表读取失败：服务暂时不可用，请稍后重试。');
  await expect(page).toHaveURL(url => url.pathname === `${root}/source`);
  data.controls.failOffset = null;
  await list.getByRole('button', { name: '重新读取分集', exact: true }).click();
  await expect(list.getByRole('button', { name: /^第 \d+ 集 · / })).toHaveCount(2);
  await expect(list.getByRole('alert')).toHaveCount(0);
  await expect(list.getByRole('button', { name: '第 01 集 · 归来的旅人', exact: true })).toHaveAttribute('aria-current', 'true');
  await page.locator('.episode-switch').click();
  await expect(list).toBeHidden();
  expect(data.listRequests).toEqual([{ offset: 0, limit: 20 }, { offset: 0, limit: 20 }]);
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('episode pagination preserves the first twenty items through a failed next-page read and deduplicates its retry', async ({ page }) => {
  const data = await episodeSwitchFixture(page, 24);
  await page.goto(`${root}/source`);
  await page.locator('.episode-switch').click();
  const list = page.locator('.episode-switch-list');
  const entries = list.getByRole('button', { name: /^第 \d+ 集 · / });
  await expect(entries).toHaveCount(20);
  data.controls.failOffset = 20;
  await list.getByRole('button', { name: '加载更多分集', exact: true }).click();
  await expect(list.getByRole('alert')).toBeVisible();
  await expect(entries).toHaveCount(20);
  await expect(list.getByRole('button', { name: '第 01 集 · 归来的旅人', exact: true })).toHaveAttribute('aria-current', 'true');
  data.controls.failOffset = null;
  data.controls.overlapNextPage = true;
  await list.getByRole('button', { name: '重新读取分集', exact: true }).click();
  await expect(entries).toHaveCount(24);
  expect(await entries.allTextContents()).toEqual(data.episodes.map(episode =>
    `第 ${String(episode.episode_number).padStart(2, '0')} 集 · ${episode.title}`));
  await expect(list.getByRole('alert')).toHaveCount(0);
  await expect(list.getByRole('button', { name: '加载更多分集', exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(url => url.pathname === `${root}/source`);
  expect(data.listRequests).toEqual([
    { offset: 0, limit: 20 }, { offset: 20, limit: 20 }, { offset: 20, limit: 20 },
  ]);
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});
