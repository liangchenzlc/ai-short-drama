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
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '视频提示词' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '分镜图片提示词' })).toHaveCount(0);
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  const useImageButton = page.getByRole('button', { name: '使用分镜图作为视频参考' });
  expect((await useImageButton.boundingBox())!.height).toBeGreaterThanOrEqual(28);
  const imagePrompt = page.getByRole('textbox', { name: '分镜图片提示词' });
  await imagePrompt.fill('逆光，镜头缓慢推进');
  await page.getByRole('button', { name: '收起分镜图生成' }).click();
  await page.getByRole('button', { name: '展开分镜图生成' }).click();
  await expect(imagePrompt).toHaveText('逆光，镜头缓慢推进');
  await page.getByRole('button', { name: '移除参考：林夏' }).click();
  await expect(page.getByRole('button', { name: '移除参考：林夏' })).toHaveCount(0);
  await page.getByRole('button', { name: '镜头 02', exact: true }).click();
  await page.getByRole('button', { name: '镜头 01', exact: true }).click();
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
  await page.getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('checkbox', { name: '选择资产：林夏' }).check();
  await page.getByRole('button', { name: '列表展示', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: '选择资产：林夏' })).toBeChecked();
  await page.getByRole('button', { name: '单卡展示', exact: true }).click();
  await expect(page.getByText('已选 1 项', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /上一个|下一个/ })).toHaveCount(0);
});

test('分镜勾选跨视图保留并在切集后清除', async ({ page }) => {
  await page.goto(preview + '?scenario=series');
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('checkbox', { name: '选择分镜：01' }).check();
  await page.getByRole('button', { name: '列表展示', exact: true }).click();
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
  await page.getByRole('button', { name: '宫格展示', exact: true }).click();
  await page.getByRole('combobox', { name: '标签筛选' }).click();
  await page.getByRole('combobox', { name: '标签筛选' }).press('Enter');
  await page.getByRole('checkbox', { name: '全选筛选结果' }).check();
  await expect(page.getByText('已选 101 项', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '批量生成图片', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量生成' });
  await expect(dialog).toContainText('101 项');
  await dialog.getByRole('button', { name: '确认生成', exact: true }).click();
  await expect(page.getByText('完成 100 · 失败 1', { exact: true })).toBeVisible();
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
  await page.getByRole('button', { name: '宫格展示', exact: true }).click();
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