import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { fixture, root } from './studio-fixture';

test.use({ actionTimeout: 10000 });

const pages = [
  ['projects', '/projects', '项目管理'],
  ['project-detail', '/projects/10', '雨夜来信'],
  ['characters', '/assets/character', '全局素材库'],
  ['scenes', '/assets/scene', '全局素材库'],
  ['props', '/assets/prop', '全局素材库'],
  ['tasks-text', '/tasks/text', '任务管理'],
  ['tasks-image', '/tasks/image', '任务管理'],
  ['tasks-video', '/tasks/video', '任务管理'],
  ['tasks-audio', '/tasks/audio', '任务管理'],
  ['media-images', '/media-library/image', '资产库'],
  ['media-videos', '/media-library/video', '资产库'],
  ['ai-config', '/ai_config', 'AI 配置'],
  ['writing', `${root}/source`, '归来的旅人'],
  ['episode-assets', `${root}/assets`, '归来的旅人'],
  ['storyboard', `${root}/storyboard`, '归来的旅人'],
  ['assembly', `${root}/assembly`, '归来的旅人'],
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

for (const width of [1024, 1440, 1920]) test(`all routes use the dark workspace at ${width}px`, async ({ page }, info) => {
  test.setTimeout(90000);
  const state = await fixture(page, true);
  await page.setViewportSize({ width, height: 1000 });
  for (const [name, url, heading] of pages) {
    await page.goto(url);
    await expect(page.getByRole('heading', { name: heading, exact: true, level: 1 })).toBeVisible();
    if (name === 'projects') await expect(page.locator('.project-tile')).toHaveCount(4);
    if (name === 'storyboard') await expect(page.locator('.storyboard-summary')).toHaveCount(20);
    if (name === 'assembly') await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
    await capture(page, info, name);
    if (width === 1024 && name.startsWith('tasks-')) {
      const rows = await page.locator('.ant-table-tbody > tr.ant-table-row').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
      expect(rows.every(height => height < 170)).toBe(true);
      const modelColumn = await page.locator('.generation-task-cell').first().boundingBox();
      expect(modelColumn!.width).toBeGreaterThan(180);
      await page.locator('.ant-table-content').evaluate(node => { node.scrollLeft = node.scrollWidth; });
      await expect(name === 'tasks-image' || name === 'tasks-video'
        ? page.getByRole('link', { name: '查看作品', exact: true }).first()
        : page.getByRole('button', { name: '查看结果', exact: true }).first()).toBeInViewport();
      await capture(page, info, `${name}-scroll-actions`);
    }
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
  await project.getByLabel('默认画幅').focus();
  await project.getByLabel('默认画幅').press('ArrowDown');
  await expect(project.locator('.ant-select-dropdown').getByText('竖屏 9:16', { exact: true })).toBeVisible();
  await capture(page, info, 'project-create-select');
  await page.keyboard.press('Escape');
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

  for (const kind of ['text', 'image', 'video', 'audio']) {
    await page.goto(`/tasks/${kind}`);
    await page.locator('.studio-page-head').getByRole('button', { name: /^新建.*任务$/ }).click();
    await expect(page.locator('.ant-drawer-body')).toBeVisible();
    await capture(page, info, `create-${kind}-task`);
    if (kind === 'image') {
      await page.getByRole('button', { name: '选择图片', exact: true }).click();
      await expect(page.locator('.media-picker-dialog')).toBeVisible();
      await capture(page, info, 'image-picker');
      await page.keyboard.press('Escape');
      await page.getByRole('textbox', { name: '画面描述' }).fill('保留这段未提交的画面描述');
      await page.locator('.ant-drawer-close').click();
      await expect(page.getByRole('dialog', { name: '未保存的修改', exact: true })).toBeVisible();
      await capture(page, info, 'drawer-confirmation');
      await page.keyboard.press('Escape');
      await expect(page.getByRole('textbox', { name: '画面描述' })).toHaveValue('保留这段未提交的画面描述');
      await page.locator('.ant-drawer-close').click();
      await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改' }).click();
      continue;
    }
    await page.locator('.ant-drawer-close').click();
  }
  await page.goto('/tasks/text?task=8001');
  await expect(page.getByText('任务详情', { exact: true })).toBeVisible();
  await capture(page, info, 'task-detail');
  await page.locator('.ant-drawer-close').click();
  await page.getByRole('button', { name: '批次记录' }).click();
  await expect(page.getByText('批次记录', { exact: true }).last()).toBeVisible();
  await capture(page, info, 'batch-history');
  await page.getByRole('button', { name: '查看批次' }).click();
  await expect(page.getByText('批量生成进度', { exact: true })).toBeVisible();
  await capture(page, info, 'batch-progress');

  await page.goto('/media-library/image');
  await page.locator('.media-gallery-card').first().click();
  await expect(page.getByRole('dialog', { name: '资产详情', exact: true })).toBeVisible();
  await capture(page, info, 'media-detail');
  await page.locator('.asset-detail-preview').getByRole('button', { name: /^预览/ }).click();
  await expect(page.locator('.image-preview-dialog')).toBeVisible();
  await capture(page, info, 'image-preview');
  await page.keyboard.press('Escape');
  await expect(page.getByText('资产详情', { exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('production editor overlays and compact desktop layout remain usable', async ({ page }, info) => {
  test.setTimeout(90000);
  const state = await fixture(page, true);
  await page.setViewportSize({ width: 1280, height: 960 });
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新建角色', exact: true }).first().click();
  await capture(page, info, 'asset-create');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(page.getByRole('heading', { name: '图片与生成', exact: true })).toBeVisible();
  await capture(page, info, 'asset-edit');
  await page.getByRole('button', { name: '返回素材列表', exact: true }).click();

  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  await page.getByRole('tab', { name: '分镜图', exact: true }).click();
  await capture(page, info, 'storyboard-image');
  await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(page.locator('.shot-video-workspace')).toBeVisible();
  await capture(page, info, 'storyboard-video');
  await page.getByRole('button', { name: /对白|声音表演/ }).click();
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
  await page.getByRole('button', { name: '收起声音编辑', exact: true }).click();
  await page.getByRole('button', { name: '导出记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '导出记录', exact: true })).toBeVisible();
  await capture(page, info, 'export-history');
  await page.keyboard.press('Escape');
  const exportButton = page.locator('.assembly-export-actions').getByRole('button', { name: /导出成片$/ });
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
  await expect(history.getByRole('button', { name: '设为当前成片', exact: true })).toBeVisible();
  await capture(page, info, 'export-history-populated');
  await history.getByRole('button', { name: '设为当前成片', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '设为当前成片', exact: true })).toBeVisible();
  await capture(page, info, 'export-apply-confirm');
  expect(state.requests.filter(r => /\/ai\/generations\/(text|image|video|audio)$|discover-models|\/exports$/.test(r.path) && r.method === 'POST')).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('video frame pickers keep registered IDs hidden and preserve selected request values', async ({ page }, info) => {
  const state = await fixture(page, true);
  const submissions: any[] = [];
  await page.route('**/api/v1/ai/generations/video', async route => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({ status: 503, json: { error: { code: 'FIXTURE', message: '测试服务暂不可用，表单保留。' } } });
  });
  await page.goto('/tasks/video');
  await page.locator('.studio-page-head').getByRole('button', { name: '新建视频任务', exact: true }).click();
  const frames = page.locator('.frame-picker');
  await expect(frames.locator('input:not([type="hidden"])')).toHaveCount(0);
  await expect(frames.locator('input[type="hidden"]').first()).toBeHidden();
  await frames.nth(0).getByRole('button', { name: '选择图片', exact: true }).click();
  await page.getByRole('dialog', { name: '从图片资产库选择' }).getByRole('button', { name: '选择此图片', exact: true }).nth(0).click();
  await frames.nth(1).getByRole('button', { name: '选择图片', exact: true }).click();
  await page.getByRole('dialog', { name: '从图片资产库选择' }).getByRole('button', { name: '选择此图片', exact: true }).nth(1).click();
  await capture(page, info, 'video-selected-frames');
  await frames.nth(1).getByRole('button', { name: '移除', exact: true }).click();
  await page.getByRole('textbox', { name: '画面描述' }).fill('雨夜中列车缓缓驶入站台。');
  const submit = page.getByRole('button', { name: /提交视频任务$/ });
  await submit.click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0].input).toEqual({ prompt: '雨夜中列车缓缓驶入站台。', first_frame_media_id: '9901' });
  await expect(page.getByRole('alert')).toBeVisible();
  await frames.nth(1).getByRole('button', { name: '选择图片', exact: true }).click();
  await page.getByRole('dialog', { name: '从图片资产库选择' }).getByRole('button', { name: '选择此图片', exact: true }).nth(1).click();
  await expect(submit).not.toHaveClass(/ant-btn-loading/);
  await submit.click();
  await expect.poll(() => submissions.length).toBe(2);
  expect(submissions[1].input).toEqual({ prompt: '雨夜中列车缓缓驶入站台。', first_frame_media_id: '9901', last_frame_media_id: '9902' });
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
  await page.getByRole('searchbox', { name: '搜索项目名称' }).fill('不存在的测试项目');
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
