import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

test.use({ actionTimeout: 10000 });

const pages = [
  ['projects', '/projects', '项目管理'],
  ['media-all', '/media-library/all', '资产库'],
  ['media-audio', '/media-library/audio', '资产库'],
  ['media-images', '/media-library/image', '资产库'],
  ['media-videos', '/media-library/video', '资产库'],
  ['ai-config', '/ai_config', 'AI 配置'],
  ['writing', `${root}/source`, '雨夜来信'],
  ['creation-assets', `${root}/assets`, '雨夜来信'],
  ['storyboard', `${root}/storyboard`, '雨夜来信'],
  ['assembly', `${root}/assembly`, '雨夜来信'],
  ['not-found', '/missing', '没有找到内容'],
] as const;

async function fits(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
  // Visible content panels must not retain one of the discarded light themes.
  const brightPanels = await page.locator('.overview-card, .writing-model-panel, .writing-editor-panel, .storyboard-tools, .storyboard-editor, .storyboard-result-preview, .config-table-wrap, .ui-dialog, .ant-drawer-body, .assembly-export-bar').evaluateAll(nodes => nodes.filter(node => {
    const rect = node.getBoundingClientRect();
    const color = getComputedStyle(node).backgroundColor.match(/[\d.]+/g)?.map(Number) ?? [];
    return rect.width > 50 && rect.height > 30 && color.length >= 3 && (color[3] ?? 1) > .8 && Math.min(...color.slice(0, 3)) > 170;
  }).map(node => node.className));
  expect(brightPanels).toEqual([]);
}

async function capture(page: Page, info: TestInfo, name: string) {
  await fits(page);
  const overlay = await page.locator('dialog[open], .ant-drawer-open').count() > 0;
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: !overlay, animations: 'disabled' });
}

for (const width of [1280, 1440, 1920]) test(`all routes use the dark workspace at ${width}px`, async ({ page }, info) => {
  test.setTimeout(90000);
  const state = await fixture(page, true);
  await page.setViewportSize({ width, height: 1000 });
  for (const [name, url, heading] of pages) {
    if (url.startsWith(root) && new URL(page.url()).pathname.startsWith(root)) {
      const stage = url.split('/').at(-1)!;
      await page.getByRole('navigation', { name: '创作流程', exact: true }).getByRole('button', {
        name: ({ assets: '资产生成', storyboard: '分镜制作', assembly: '成片预览' } as Record<string, string>)[stage], exact: true,
      }).click();
      await expect(page).toHaveURL(current => current.pathname === url);
    } else {
      if (new URL(page.url()).pathname.startsWith(root)) await expect(page.locator('.creation-save-state')).toHaveAttribute('aria-label', '保存状态：已保存');
      await page.goto(url);
    }
    if (url.startsWith(root)) {
      await expect(page.locator('.creation-project-location')).toContainText(heading);
      await expect(page.getByRole('navigation', { name: '创作流程', exact: true })).toBeVisible();
    } else await expect(page.getByRole('heading', { name: heading, exact: true, level: 1 })).toBeVisible();
    if (name === 'projects') await expect(page.locator('.project-tile')).toHaveCount(4);
    if (name === 'storyboard') await expect(page.locator('.storyboard-summary')).toHaveCount(20);
    if (name === 'assembly') await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
    await capture(page, info, name);
  }
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('dialogs, drawers and nested confirmations retain input and restore keyboard focus', async ({ page }, info) => {
  test.setTimeout(90000);
  const state = await fixture(page, true);
  await page.goto('/projects');
  const create = page.getByRole('button', { name: '新建项目', exact: true });
  await create.click();
  const project = page.getByRole('dialog', { name: '新建项目', exact: true });
  await expect(project.getByLabel('项目名称')).toBeFocused();
  await project.getByLabel('项目名称').fill('键盘切换时保留的项目名称');
  const canvasMode = project.getByRole('radio', { name: '无限画布模式', exact: true });
  await canvasMode.focus();
  await canvasMode.press('Space');
  await expect(canvasMode).toBeChecked();
  await expect(project.getByLabel('项目名称')).toHaveValue('键盘切换时保留的项目名称');
  await capture(page, info, 'project-create-mode');
  await expect(project).toBeVisible();
  await project.getByRole('button', { name: '取消', exact: true }).click();
  await expect(create).toBeFocused();

  await page.goto('/ai_config');
  await page.getByRole('button', { name: '添加文本模型' }).click();
  const config = page.getByRole('dialog', { name: /添加|新建/ });
  await config.getByRole('textbox', { name: '配置名称' }).fill('未保存的配置');
  await capture(page, info, 'ai-config-form');
  await config.getByRole('button', { name: '取消', exact: true }).click();
  const confirm = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirm).toBeVisible();
  await expect(confirm.getByRole('button', { name: '取消', exact: true })).toBeFocused();
  await capture(page, info, 'nested-confirmation');
  await page.keyboard.press('Tab');
  await expect(confirm.getByRole('button', { name: '放弃修改' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(confirm.getByRole('button', { name: '关闭弹窗' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(confirm).toHaveCount(0);
  await expect(config.getByRole('textbox', { name: '配置名称' })).toHaveValue('未保存的配置');
  await config.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改' }).click();
  await expect(config).toHaveCount(0);
  expect(state.requests.filter(r => r.path === '/ai-model-configs' && r.method !== 'GET')).toEqual([]);


  await page.goto('/media-library/image');
  await page.locator('.media-gallery-card').first().click();
  await expect(page.locator('.ant-image-preview')).toBeVisible();
  await capture(page, info, 'image-preview');
  await page.keyboard.press('Escape');
  await expect(page.locator('.ant-image-preview')).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('production editor overlays and compact desktop layout remain usable', async ({ page }, info) => {
  test.setTimeout(90000);
  const state = await fixture(page, true);
  await page.setViewportSize({ width: 1280, height: 960 });
  await page.goto(`${root}/assets`);
  await page.locator('.standard-assets-filters').getByRole('button', { name: '角色', exact: true }).click();
  await page.getByRole('button', { name: '创建资产', exact: true }).click();
  await page.getByRole('textbox', { name: '素材名称', exact: true }).fill('站台旅客');
  await expect.poll(() => state.requests.some(request => request.method === 'POST' && request.path === `${root}/assets` && request.body.name === '站台旅客')).toBe(true);
  await expect(page).toHaveURL(url => url.searchParams.get('asset') === '504');
  await capture(page, info, 'asset-create');
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(page.locator('.creation-asset-detail')).toBeVisible();
  await expect(page.getByRole('textbox', { name: '素材名称', exact: true })).toHaveValue('林晚');
  await capture(page, info, 'asset-edit');

  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  await page.getByRole('tab', { name: '分镜图', exact: true }).click();
  await capture(page, info, 'storyboard-image');
  await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(page.locator('.shot-video-workspace')).toBeVisible();
  await capture(page, info, 'storyboard-video');
  await page.locator('.storyboard-dialogue-settings > summary').click();
  await page.getByRole('button', { name: '编辑并确认对白', exact: true }).click();
  await expect(page.locator('.native-dialogue-dialog')).toBeVisible();
  await capture(page, info, 'native-dialogue');
  await page.keyboard.press('Escape');

  await page.goto(`${root}/assembly`);
  await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  await page.getByRole('button', { name: '专注剪辑', exact: true }).click();
  await capture(page, info, 'assembly-focus');
  await page.getByRole('button', { name: '退出专注剪辑', exact: true }).click();
  await page.getByRole('button', { name: /声音|配音.*字幕/ }).click();
  await expect(page.getByRole('region', { name: '原声、字幕和配乐', exact: true })).toBeVisible();
  await capture(page, info, 'sound-subtitles');
  await page.getByRole('tab', { name: /配乐/ }).click();
  await capture(page, info, 'sound-music');
  await page.getByRole('region', { name: '原声、字幕和配乐', exact: true }).getByRole('button', { name: '收起声音编辑', exact: true }).click();
  await page.getByRole('button', { name: '导出记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '导出记录', exact: true })).toBeVisible();
  await capture(page, info, 'export-history');
  await page.keyboard.press('Escape');
  const exportButton = page.getByRole('button', { name: '导出成片', exact: true });
  await expect(exportButton).not.toHaveClass(/ant-btn-loading/);
  await exportButton.click();
  await expect(page.getByRole('dialog', { name: '导出本集成片', exact: true })).toBeVisible();
  await capture(page, info, 'export-confirm');
  await page.getByRole('dialog', { name: '导出本集成片', exact: true }).getByRole('button', { name: '返回编辑' }).click();
  await page.getByRole('button', { name: '同步分镜视频', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '同步分镜视频', exact: true })).toBeVisible();
  await capture(page, info, 'assembly-sync-confirm');
  await page.keyboard.press('Escape');
  await page.route(`**/api/v1${root}/assembly/exports**`, async route => {
    expect(route.request().method()).toBe('GET');
    await route.fulfill({ json: { items: [{ id: '78001', kind: 'export', status: 'succeeded', stage: 'complete', media_id: '78002', context_hash: 'a'.repeat(64), is_stale: false, created_at: '2026-09-30T08:00:00Z', progress: 100 }], has_more: false } });
  });
  await page.getByRole('button', { name: '导出记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '导出记录', exact: true });
  await history.getByRole('button', { name: '更多导出操作', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '设为当前成片', exact: true })).toBeVisible();
  await capture(page, info, 'export-history-populated');
  await page.getByRole('menuitem', { name: '设为当前成片', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '设为当前成片', exact: true })).toBeVisible();
  await capture(page, info, 'export-apply-confirm');
  expect(state.requests.filter(r => /\/ai\/generations\/(text|image|video|audio)$|discover-models|\/exports$/.test(r.path) && r.method === 'POST')).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});


test('loading, empty, search and error states provide a clear next action', async ({ page }, info) => {
  const state = await fixture(page, true);
  state.controls.delayProjects = 1600;
  await page.goto('/projects');
  await expect(page.getByRole('status', { name: '正在加载项目' })).toBeVisible();
  await capture(page, info, 'projects-loading');
  await expect(page.locator('.project-tile')).toHaveCount(4);
  state.controls.delayProjects = 0;
  state.controls.emptyProjects = true;
  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: '开始你的第一部短剧' })).toBeVisible();
  await capture(page, info, 'projects-empty');
  state.controls.emptyProjects = false;
  await page.reload();
  await expect(page.locator('.project-tile')).toHaveCount(4);
  const search = page.getByRole('textbox', { name: '搜索项目名称', exact: true });
  await search.fill('不存在的测试项目');
  await search.press('Enter');
  await expect(page.getByRole('heading', { name: '没有找到项目' })).toBeVisible();
  await capture(page, info, 'projects-no-results');
  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await expect(page.locator('.project-tile')).toHaveCount(4);
  state.controls.errorProjects = true;
  await page.reload();
  await expect(page.getByRole('button', { name: '重试', exact: true })).toBeVisible();
  await capture(page, info, 'projects-error');
  state.controls.errorProjects = false;
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.locator('.project-tile')).toHaveCount(4);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});
