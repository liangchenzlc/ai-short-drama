import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const preview = '/e2e/creation/fixtures/workbenchPreview.html';

test.beforeAll(() => {
  const frontend = process.cwd();
  const target = path.join(frontend, '.runtime', 'workbenchPreview');
  const source = path.resolve(frontend, '..', 'docs', 'design', 'standard-creation-workflow', 'assets');
  mkdirSync(target, { recursive: true });
  for (const name of ['woman.jpg', 'street.jpg', 'camera.jpg']) {
    copyFileSync(path.join(source, name), path.join(target, name));
  }
  if (!existsSync(path.join(frontend, '.runtime', 'timeline-fixture.mp4'))) {
    execFileSync('powershell.exe', [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
      path.join(frontend, 'scripts', 'prepare-timeline-acceptance.ps1'),
    ], { cwd: frontend, stdio: 'inherit' });
  }
});

test('演示入口与真实接口隔离', async ({ page }) => {
  const calls: string[] = [];
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === origin && !url.pathname.startsWith('/api/')) {
      await route.continue();
      return;
    }
    calls.push(url.href);
    await route.abort();
  });
  await page.goto(preview + '?scenario=single');
  await expect(page.getByRole('status', { name: '演示数据' })).toBeVisible();
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await expect(page.getByRole('button', { name: '林夏', exact: true })).toBeVisible();
  expect(calls).toEqual([]);
});

test('单剧增加分集后分别编辑剧本', async ({ page }) => {
  await page.goto(preview + '?scenario=single');
  await page.getByRole('tab', { name: '剧本', exact: true }).click();
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.fill('第一集的剧本');
  await expect(page.getByRole('button', { name: /当前分集/ })).toHaveCount(0);
  await page.getByRole('button', { name: '添加分集', exact: true }).click();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('menuitem', { name: '第 02 集', exact: true }).click();
  await editor.fill('第二集的剧本');
  await editor.press('Control+z');
  await expect(editor).not.toHaveValue('第一集的剧本');
  await editor.fill('第二集的剧本');
  await page.getByRole('button', { name: '当前分集：第 02 集' }).click();
  await page.getByRole('menuitem', { name: '第 01 集', exact: true }).click();
  await expect(editor).toHaveValue('第一集的剧本');
});

test('AI 概要采用空白首集并追加其余分集', async ({ page }) => {
  await page.goto(preview + '?scenario=single');
  await page.getByRole('button', { name: '生成分集概要', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '生成分集概要' });
  await dialog.getByRole('spinbutton', { name: '生成集数' }).fill('3');
  await dialog.getByRole('button', { name: '生成概要', exact: true }).click();
  await dialog.getByRole('button', { name: '采用概要', exact: true }).click();
  await expect(page.getByRole('list', { name: '分集概要' }).getByRole('listitem')).toHaveCount(3);
  const first = page.getByRole('textbox', { name: '第 01 集概要' });
  await first.fill('用户已经写好的概要');
  await page.getByRole('button', { name: '生成分集概要', exact: true }).click();
  await dialog.getByRole('spinbutton', { name: '生成集数' }).fill('2');
  await dialog.getByRole('button', { name: '生成概要', exact: true }).click();
  await dialog.getByRole('button', { name: '采用概要', exact: true }).click();
  await expect(first).toHaveValue('用户已经写好的概要');
  await expect(page.getByRole('list', { name: '分集概要' }).getByRole('listitem')).toHaveCount(5);
});
test('角色画像和图片组区分查看与主图', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '林夏', exact: true }).click();
  await expect(page.getByLabel('姓名', { exact: true })).toHaveValue('林夏');
  await expect(page.getByLabel('身份', { exact: true })).toHaveValue('记者');
  const imageGeometry = await page.locator('.imageGalleryCanvas').evaluate((canvas) => {
    const image = canvas.querySelector('img')!;
    return { image: image.getBoundingClientRect().height, canvas: canvas.getBoundingClientRect().height };
  });
  expect(imageGeometry.image).toBeLessThanOrEqual(imageGeometry.canvas + 1);
  await page.getByRole('button', { name: '查看图片 2', exact: true }).click();
  await expect(page.getByRole('img', { name: '当前查看图片 2' })).toBeVisible();
  await expect(page.getByRole('button', { name: '图片 1，主图' })).toBeVisible();
  await page.getByRole('button', { name: '设为主图', exact: true }).click();
  await expect(page.getByRole('button', { name: '图片 2，主图' })).toBeVisible();
  const prompt = page.getByRole('textbox', { name: '资产图片提示词' });
  await prompt.fill('@');
  await expect(page.getByRole('listbox', { name: '引用参考图片' })).toBeVisible();
  const tags = page.getByRole('combobox', { name: '资产标签' });
  await tags.fill('第 02 集');
  await tags.press('Enter');
  await expect(page.getByRole('button', { name: '图片 2，主图' })).toBeVisible();
  await page.getByRole('button', { name: '咖啡馆', exact: true }).click();
  await page.getByRole('button', { name: '林夏', exact: true }).click();
  await expect(page.getByRole('button', { name: '图片 2，主图' })).toBeVisible();
});
test('分镜图展开编辑且视频参考可移除', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '视频提示词' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '分镜图片提示词' })).toHaveCount(0);
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeInViewport();
  await expect(page.getByRole('textbox', { name: '视频提示词' })).toBeInViewport();
  const useImageButton = page.getByRole('button', { name: '使用分镜图作为视频参考' });
  expect((await useImageButton.boundingBox())!.height).toBeGreaterThanOrEqual(28);
  const imagePrompt = page.getByRole('textbox', { name: '分镜图片提示词' });
  await imagePrompt.fill('逆光，镜头缓慢推进');
  await page.getByRole('button', { name: '收起分镜图生成' }).click();
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  await expect(imagePrompt).toHaveText('逆光，镜头缓慢推进');
  await page.getByRole('button', { name: '移除参考：林夏' }).click();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toHaveCount(0);
  await page.getByRole('button', { name: '选择分镜 02', exact: true }).click();
  await page.getByRole('button', { name: '选择分镜 01', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toHaveCount(0);
  await page.getByRole('button', { name: '使用分镜图作为视频参考' }).click();
  await expect(page.getByRole('button', { name: '移除参考：分镜图' })).toBeVisible();
});

test('两种视频路径保留当前视频', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toBeVisible();
  await expect(page.getByRole('button', { name: '移除参考：分镜图' })).toHaveCount(0);
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await expect(page.getByRole('status', { name: '视频生成状态' })).toHaveText('已完成');
  await page.getByRole('button', { name: '使用分镜图作为视频参考' }).click();
  await expect(page.getByRole('button', { name: '移除参考：分镜图' })).toBeVisible();
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await expect(page.getByRole('status', { name: '视频生成状态' })).toHaveText('已完成');
  await expect(page.getByText('候选视频 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '预览候选视频 1' }).click();
  await expect(page.getByText('正在预览候选视频 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '采用候选视频 1' }).click();
  await expect(page.getByRole('status', { name: '视频生成状态' })).toHaveText('已采用');
});
test('视图切换保留勾选', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('checkbox', { name: '选择资产：林夏' }).check();
  await page.locator('.resourceCollection').getByRole('button', { name: '列表展示', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: '选择资产：林夏' })).toBeChecked();
  await page.locator('.resourceCollection').getByRole('button', { name: '单卡展示', exact: true }).click();
  await expect(page.getByText('已选 1 项', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /上一个|下一个/ })).toHaveCount(0);
});

test('分镜勾选跨视图保留并在切集后清除', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('checkbox', { name: '选择分镜：01' }).check();
  await page.locator('.resourceCollection').getByRole('button', { name: '列表展示', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: '选择分镜：01' })).toBeChecked();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('menuitem', { name: '第 02 集', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: '选择分镜：01' })).not.toBeChecked();
  await expect(page.getByText('已选 0 项', { exact: true })).toBeVisible();
});
test('批量包含全部筛选结果且可重试失败项', async ({ page }) => {
  await page.goto(preview + '?scenario=batch');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('combobox', { name: '标签筛选' }).click();
  await page.getByRole('combobox', { name: '标签筛选' }).press('Enter');
  await page.getByRole('checkbox', { name: '全选筛选结果' }).check();
  await expect(page.getByText('已选 101 项', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '批量生成图片', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量生成' });
  await expect(dialog).toContainText('101 项');
  await dialog.getByRole('button', { name: '确认生成', exact: true }).click();
  await expect(page.getByText('完成 100 · 失败 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '批量记录', exact: true }).click();
  const records = page.getByRole('list', { name: '批次记录' }).getByRole('listitem');
  await expect(records).toHaveCount(2);
  await expect(records.nth(0)).toContainText('100 项');
  await expect(records.nth(1)).toContainText('1 项');
  await page.getByRole('button', { name: '批量记录', exact: true }).click();
  await page.getByRole('button', { name: '重试失败项', exact: true }).click();
  await expect(page.getByText('完成 101 · 失败 0', { exact: true })).toBeVisible();
});
test('分镜批量任务保留所属分集且不访问真实服务', async ({ page }) => {
  const blocked: string[] = [];
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === origin && !url.pathname.startsWith('/api/')) return route.continue();
    blocked.push(url.href);
    await route.abort();
  });
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('checkbox', { name: '选择分镜：02' }).check();
  await page.getByRole('button', { name: '批量生成视频', exact: true }).click();
  await page.getByRole('dialog', { name: '批量生成' })
    .getByRole('button', { name: '确认生成', exact: true }).click();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('menuitem', { name: '第 02 集', exact: true }).click();
  await expect(page.getByText('完成 1 · 失败 0', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '当前分集：第 02 集' }).click();
  await page.getByRole('menuitem', { name: '第 01 集', exact: true }).click();
  await expect(page.getByText('完成 1 · 失败 0', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '打开镜头 02' })).toContainText('视频已生成');
  expect(blocked).toEqual([]);
});
test('桌面工作台有助手时仍可操作', async ({ page }, info) => {
  for (const width of [1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(preview + '?scenario=series');
    await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
    await page.getByRole('navigation', { name: '创作流程' })
      .getByRole('button', { name: '资产生成', exact: true }).click();
    await page.getByRole('button', { name: '林夏', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '资产图片提示词' })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('asset-' + width + '.png'), animations: 'disabled' });
  }
});

test('完整演示不访问真实服务', async ({ page }) => {
  const blocked: string[] = [];
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === origin && !url.pathname.startsWith('/api/')) return route.continue();
    blocked.push(url.href);
    await route.abort();
  });
  await page.goto(preview + '?scenario=series');
  await page.getByRole('tab', { name: '剧本', exact: true }).click();
  await page.getByRole('textbox', { name: '剧本正文' }).fill('第一集演示剧本');
  const nav = page.getByRole('navigation', { name: '创作流程' });
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '林夏', exact: true }).click();
  await page.getByRole('button', { name: '查看图片 2', exact: true }).click();
  await page.getByRole('button', { name: '设为主图', exact: true }).click();
  const tags = page.getByRole('combobox', { name: '资产标签' });
  await tags.fill('第 02 集');
  await tags.press('Enter');
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toBeVisible();
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  await page.getByRole('textbox', { name: '分镜图片提示词' }).fill('缓慢推进');
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect(page.getByRole('status', { name: '分镜图生成状态' })).toHaveText('已完成');
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await expect(page.getByRole('status', { name: '视频生成状态' })).toHaveText('已完成');
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('checkbox', { name: '选择分镜：02' }).check();
  await page.getByRole('button', { name: '批量生成视频', exact: true }).click();
  await page.getByRole('dialog', { name: '批量生成' })
    .getByRole('button', { name: '确认生成', exact: true }).click();
  await expect(page.getByText('完成 1 · 失败 0', { exact: true })).toBeVisible();
  expect(blocked).toEqual([]);
});
test('刷新预览重置演示数据', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByLabel('身份', { exact: true }).fill('编辑过的身份');
  await page.getByRole('button', { name: '查看图片 2' }).click();
  await page.getByRole('button', { name: '设为主图' }).click();
  await page.reload();
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await expect(page.getByLabel('身份', { exact: true })).toHaveValue('记者');
  await expect(page.getByRole('button', { name: '图片 1，主图' })).toBeVisible();
  await expect(page.getByRole('status', { name: '演示数据' })).toBeVisible();
});
test('新分集可添加镜头并默认引用关联资产', async ({ page }) => {
  await page.goto(preview + '?scenario=single');
  await page.getByRole('button', { name: '添加分集', exact: true }).click();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('menuitem', { name: '第 02 集', exact: true }).click();
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByText('本集暂无镜头', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '创建分镜', exact: true }).click();
  await page.getByRole('textbox', { name: '镜头名称', exact: true }).fill('第二集镜头');
  await page.getByRole('button', { name: '关联资产', exact: true }).click();
  await page.getByRole('checkbox', { name: '关联资产：林夏' }).check();
  await page.getByRole('checkbox', { name: '关联资产：咖啡馆' }).check();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toBeVisible();
  await expect(page.getByRole('button', { name: '移除参考：咖啡馆' })).toBeVisible();
  await page.getByRole('button', { name: '移除参考：林夏' }).click();
  await page.getByRole('button', { name: '当前分集：第 02 集' }).click();
  await page.getByRole('menuitem', { name: '第 01 集', exact: true }).click();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('menuitem', { name: '第 02 集', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '镜头名称', exact: true })).toHaveValue('第二集镜头');
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '移除参考：咖啡馆' })).toBeVisible();
});
test('保留原有左右独立的资源列表操作', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  const nav = page.getByRole('navigation', { name: '创作流程' });
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  const assets = page.locator('.standard-assets-list');
  await expect(assets.getByRole('heading', { name: /资产库/ })).toBeVisible();
  await expect(assets.getByRole('textbox', { name: '搜索素材' })).toBeVisible();
  await expect(assets.getByRole('button', { name: '角色', exact: true })).toBeVisible();
  await expect(assets.getByRole('button', { name: '创建资产' })).toBeVisible();
  await assets.getByRole('button', { name: '列表展示' }).click();
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示' }).click();
  await expect(assets.getByRole('button', { name: '列表展示' })).toHaveAttribute('aria-pressed', 'true');
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  const shots = page.locator('.standard-storyboard-list');
  await expect(shots.getByRole('heading', { name: /分镜表/ })).toBeVisible();
  await expect(shots.getByRole('textbox', { name: '搜索镜头名称' })).toBeVisible();
  await expect(shots.getByRole('button', { name: '待制作', exact: true })).toBeVisible();
  await expect(shots.getByRole('button', { name: '创建分镜' })).toBeVisible();
});
test('采用首集概要时保留已设置的集名', async ({ page }) => {
  await page.goto(preview + '?scenario=single');
  await page.getByRole('button', { name: '添加分集', exact: true }).click();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('button', { name: '重命名第 01 集' }).click();
  await page.getByLabel('分集名称').fill('黎明');
  await page.getByLabel('分集名称').press('Enter');
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('button', { name: '生成分集概要', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '生成分集概要' });
  await dialog.getByRole('spinbutton', { name: '生成集数' }).fill('1');
  await dialog.getByRole('button', { name: '生成概要', exact: true }).click();
  await dialog.getByRole('button', { name: '采用概要', exact: true }).click();
  await expect(page.getByRole('button', { name: '当前分集：第 01 集' })).toContainText('黎明');
});
test('更改素材类型前提示角色画像会被清除', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.locator('.assetDetails .ant-select').first().click();
  await page.locator('.ant-select-dropdown:visible').getByText('场景', { exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '更改素材类型' });
  await expect(dialog).toContainText('角色画像');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByLabel('身份', { exact: true })).toHaveValue('记者');
});
test('新增关联时只默认选入角色和场景的图片', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  const nav = page.getByRole('navigation', { name: '创作流程' });
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '信封', exact: true }).click();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '关联资产', exact: true }).click();
  await page.getByRole('checkbox', { name: '关联资产：信封' }).check();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toBeVisible();
  await expect(page.getByRole('button', { name: '移除参考：咖啡馆' })).toBeVisible();
  await expect(page.getByRole('button', { name: '移除参考：信封' })).toHaveCount(0);
});
test('图片组失败位置可单独重试且不替换主图', async ({ page }) => {
  await page.goto(preview + '?scenario=partial');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '重试图片 2', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看图片 2', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '图片 1，主图' })).toBeVisible();
});
test('原左栏可创建和删除资产与镜头', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  const nav = page.getByRole('navigation', { name: '创作流程' });
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '创建资产' }).click();
  await page.getByLabel('姓名', { exact: true }).fill('新角色');
  const assetCard = page.locator('.standard-asset-card').filter({ has: page.getByRole('button', { name: '新角色', exact: true }) });
  await assetCard.hover();
  await assetCard.getByRole('button', { name: '新角色卡片更多操作' }).click();
  await page.getByRole('menuitem', { name: '删除素材' }).click();
  await page.getByRole('dialog', { name: '删除素材' }).getByRole('button', { name: '确认移除' }).click();
  await expect(page.getByRole('button', { name: '新角色', exact: true })).toHaveCount(0);
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '创建分镜' }).click();
  await page.getByRole('textbox', { name: '镜头名称', exact: true }).fill('新镜头');
  const shotCard = page.locator('.storyboard-shot-card').filter({ has: page.getByRole('button', { name: '选择分镜 03' }) });
  await shotCard.hover();
  await shotCard.getByRole('button', { name: '分镜 03 卡片更多操作' }).click();
  await page.getByRole('menuitem', { name: '删除分镜' }).click();
  await page.getByRole('dialog', { name: '删除分镜' }).getByRole('button', { name: '确认移除' }).click();
  await expect(page.getByRole('button', { name: '选择分镜 03' })).toHaveCount(0);
});
test('参考图超限时说明原因并可移除后生成', async ({ page }) => {
  await page.goto(preview + '?scenario=referenceLimit');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('button', { name: '生成视频', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '生成受阻：当前模型最多支持 4 张参考图片，请移除多余图片。' }).click();
  await expect(page.getByText('当前模型最多支持 4 张参考图片，请移除多余图片。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '移除参考：参考角色 3' }).click();
  await expect(page.getByRole('button', { name: '生成视频', exact: true })).toBeEnabled();
});
test('四张新图支持比较并保留历史主图', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看图片 4', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '查看当前主图' })).toBeVisible();
  await page.getByRole('button', { name: '查看全部', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '全部图片' }).getByRole('img')).toHaveCount(4);
  await page.getByRole('dialog', { name: '全部图片' }).getByRole('button', { name: /图片 4/ }).click();
  await expect(page.getByRole('img', { name: '当前查看图片 4' })).toBeVisible();
  await page.getByText('最新生成', { exact: true }).click();
  await page.getByText('历史图片 1', { exact: true }).last().click();
  await expect(page.getByRole('button', { name: '图片 1，主图' })).toBeVisible();
});

test('审查回归：批量生成只处理当前筛选范围', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('checkbox', { name: '全选筛选结果' }).check();
  await page.getByRole('combobox', { name: '标签筛选' }).click();
  await page.getByRole('combobox', { name: '标签筛选' }).press('Enter');
  await page.getByRole('button', { name: '批量生成图片', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量生成' });
  await expect(dialog).toContainText('筛选外 1 项');
  await expect(dialog).toContainText('可执行 0 项');
  await expect(dialog.getByRole('button', { name: '确认生成' })).toBeDisabled();
  await dialog.getByRole('radio', { name: '重新生成已有结果' }).check();
  await dialog.getByRole('button', { name: '确认生成' }).click();
  await expect(page.getByText('完成 2 · 失败 0', { exact: true })).toBeVisible();
  await page.locator('.resourceCollectionFilters .ant-select-clear').click();
  await page.getByRole('button', { name: '信封', exact: true }).click();
  await expect(page.getByText('暂无图片', { exact: true })).toBeVisible();
});

test('审查回归：批量视频跳过参考图超限镜头', async ({ page }) => {
  await page.goto(preview + '?scenario=referenceLimit');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示' }).click();
  await page.getByRole('checkbox', { name: '选择分镜：01' }).check();
  await page.getByRole('checkbox', { name: '选择分镜：02' }).check();
  await page.getByRole('button', { name: '批量生成视频', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量生成' });
  await expect(dialog).toContainText('可执行 1 项');
  await expect(dialog).toContainText('参考图片超限 1 项');
  await dialog.getByRole('radio', { name: '重新生成已有结果' }).check();
  await expect(dialog).toContainText('可执行 1 项');
  await dialog.getByRole('button', { name: '确认生成' }).click();
  await expect(page.getByText('完成 1 · 失败 0', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '打开镜头 01' })).toContainText('无视频');
  await expect(page.getByRole('button', { name: '打开镜头 02' })).toContainText('视频已生成');
});

test('审查回归：缺图的关联角色补图后默认作为参考，手动移除后不恢复', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  const nav = page.getByRole('navigation', { name: '创作流程' });
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '创建资产' }).click();
  await page.getByLabel('姓名', { exact: true }).fill('新角色');
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '关联资产', exact: true }).click();
  await page.getByRole('checkbox', { name: '关联资产：新角色' }).check();
  await expect(page.getByText('新角色 · 缺图', { exact: true })).toBeVisible();
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '新角色', exact: true }).click();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '移除参考：新角色' }).click();
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除参考：新角色' })).toHaveCount(0);
});

test('审查回归：删除资产清理其他资产中的图片引用', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  const prompt = page.getByRole('textbox', { name: '资产图片提示词' });
  await prompt.fill('@');
  await page.getByRole('option', { name: /咖啡馆/ }).click();
  await expect(prompt.locator('.asset-prompt-chip')).toHaveAttribute('title', '咖啡馆');
  const card = page.locator('.standard-asset-card').filter({ has: page.getByRole('button', { name: '咖啡馆', exact: true }) });
  await card.hover();
  await card.getByRole('button', { name: '咖啡馆卡片更多操作' }).click();
  await page.getByRole('menuitem', { name: '删除素材' }).click();
  await page.getByRole('dialog', { name: '删除素材' }).getByRole('button', { name: '确认移除' }).click();
  await expect(page.getByRole('button', { name: '移除图片参考：咖啡馆' })).toHaveCount(0);
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(0);
});

test('审查回归：取消关联时保持分镜图片提示词引用对象', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  const prompt = page.getByRole('textbox', { name: '分镜图片提示词' });
  await prompt.fill('@');
  await page.getByRole('option', { name: /咖啡馆/ }).click();
  const chip = prompt.locator('.asset-prompt-chip');
  await expect(chip).toHaveAttribute('data-asset-prompt-token', '@图片2');
  await page.getByRole('button', { name: '关联资产', exact: true }).click();
  await page.getByRole('checkbox', { name: '关联资产：林夏' }).uncheck();
  await expect(chip).toHaveAttribute('data-asset-prompt-token', '@图片1');
  await expect(chip).toHaveAttribute('title', '咖啡馆');
});

for (const generation of ['单次', '批量'] as const) {
  test(`审查回归：${generation}补图后分镜图片提示词仍引用原资产`, async ({ page }) => {
    await page.goto(preview + '?scenario=series');
    const nav = page.getByRole('navigation', { name: '创作流程' });
    await nav.getByRole('button', { name: '资产生成', exact: true }).click();
    await page.getByRole('button', { name: '创建资产' }).click();
    await page.getByLabel('姓名', { exact: true }).fill('新角色');
    await page.getByRole('button', { name: '生成图片', exact: true }).click();
    await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
    await page.getByRole('button', { name: '关联资产', exact: true }).click();
    await page.getByRole('checkbox', { name: '关联资产：信封' }).check();
    await page.getByRole('checkbox', { name: '关联资产：新角色' }).check();
    await page.getByRole('button', { name: '展开分镜图生成' }).click();
    const prompt = page.getByRole('textbox', { name: '分镜图片提示词' });
    await prompt.fill('@');
    await page.getByRole('option', { name: /新角色/ }).click();
    await expect(prompt.locator('.asset-prompt-chip')).toHaveAttribute('data-asset-prompt-token', '@图片3');
    await nav.getByRole('button', { name: '资产生成', exact: true }).click();
    if (generation === '单次') {
      await page.getByRole('button', { name: '信封', exact: true }).click();
      await page.getByRole('button', { name: '生成图片', exact: true }).click();
    } else {
      await page.locator('.resourceCollection').getByRole('button', { name: '宫格展示' }).click();
      await page.getByRole('checkbox', { name: '选择资产：信封' }).check();
      await page.getByRole('button', { name: '批量生成图片' }).click();
      await page.getByRole('dialog', { name: '批量生成' }).getByRole('button', { name: '确认生成' }).click();
      await expect(page.getByText('完成 1 · 失败 0', { exact: true })).toBeVisible();
    }
    await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
    await page.getByRole('button', { name: '展开分镜图生成' }).click();
    const chip = prompt.locator('.asset-prompt-chip');
    await expect(chip).toHaveAttribute('data-asset-prompt-token', '@图片4');
    await expect(chip).toHaveAttribute('title', '新角色');
  });
}

test('审查回归：多个批次补图时持续保持分镜图片引用', async ({ page }) => {
  await page.goto(preview + '?scenario=batch');
  const nav = page.getByRole('navigation', { name: '创作流程' });
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('button', { name: '创建资产' }).click();
  await page.getByLabel('姓名', { exact: true }).fill('带图角色');
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await page.getByRole('button', { name: '创建资产' }).click();
  await page.getByLabel('姓名', { exact: true }).fill('尾项');
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '关联资产', exact: true }).click();
  await page.getByRole('checkbox', { name: '关联资产：角色 101' }).check();
  await page.getByRole('checkbox', { name: '关联资产：带图角色' }).check();
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  const prompt = page.getByRole('textbox', { name: '分镜图片提示词' });
  await prompt.fill('@');
  await page.getByRole('option', { name: /带图角色/ }).click();
  await expect(prompt.locator('.asset-prompt-chip')).toHaveAttribute('data-asset-prompt-token', '@图片1');
  await nav.getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('checkbox', { name: '全选筛选结果' }).check();
  await page.getByRole('button', { name: '批量生成图片' }).click();
  await page.getByRole('dialog', { name: '批量生成' }).getByRole('button', { name: '确认生成' }).click();
  await expect(page.getByText('完成 101 · 失败 1', { exact: true })).toBeVisible();
  await nav.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  const chip = prompt.locator('.asset-prompt-chip');
  await expect(chip).toHaveAttribute('data-asset-prompt-token', '@图片4');
  await expect(chip).toHaveAttribute('title', '带图角色');
});
