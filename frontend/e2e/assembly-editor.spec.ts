import { test, expect } from '@playwright/test';

test('unplayable sources cannot enter the track and reserved shots survive editing', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/e2e/assembly-editor-fixture.html');
  const preparing = page.locator('.assembly-source-list article').nth(1);
  await expect(preparing).toHaveAttribute('draggable', 'false');
  await expect(page.getByRole('button', { name: '添加镜头 2', exact: true })).toBeDisabled();
  // Guard the mutation as well as the drag affordance, including a stale drag begun before probing failed.
  await page.locator('.assembly-timeline-canvas').evaluate(node => {
    const transfer = new DataTransfer(); transfer.setData('application/x-assembly-source', 'preparing');
    node.dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: transfer, clientX: node.getBoundingClientRect().left + 50 }));
  });
  await expect(page.getByLabel('草稿片段数量')).toHaveText('2');
  await expect(page.getByRole('alert')).toContainText('检测中');
  const problems = page.getByLabel('无法显示在时间轴的片段');
  await expect(problems).toHaveCount(0);
  await expect(page.locator('.assembly-track-clip')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '移除镜头 3 的问题片段' })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('reserved-shots-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath('reserved-shots-mobile.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: '删除片段', exact: true }).click();
  await expect(problems).toHaveCount(0);
  await expect(page.getByLabel('草稿片段数量')).toHaveText('1');
  await expect(page.getByRole('button', { name: '删除片段', exact: true })).toBeDisabled();
  await page.locator('.assembly-editing').focus();
  await page.keyboard.press('Delete');
  await expect(page.getByLabel('草稿片段数量')).toHaveText('1');
  await page.getByRole('button', { name: /^撤\s*销$/ }).click();
  await expect(problems).toHaveCount(0);
  await expect(page.getByLabel('草稿片段数量')).toHaveText('2');
  await page.getByRole('button', { name: '添加镜头 1', exact: true }).click();
  await expect(page.getByLabel('草稿片段数量')).toHaveText('3');
  await expect(page.locator('.assembly-track-clip')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('result playback resets the cursor and uses the frozen aspect and source thumbnails', async ({ page }, info) => {
  await page.goto('/e2e/assembly-editor-fixture.html');
  await page.getByRole('spinbutton', { name: '定位时间（秒）' }).fill('0.5');
  await expect(page.getByLabel('播放位置')).toContainText('00:00:15');
  await page.getByRole('button', { name: '查看冻结成片', exact: true }).click();
  await expect(page.locator('.assembly-result-screen')).toHaveClass(/is-portrait/);
  await expect(page.getByRole('spinbutton', { name: '定位时间（秒）' })).toHaveValue(/^0(?:\.0+)?$/);
  await expect(page.locator('.assembly-track-clip img')).toHaveAttribute('src', '/.runtime/timeline-filmstrip.jpg');
  await expect(page.getByLabel('无法显示在时间轴的片段')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '删除片段', exact: true })).toBeDisabled();
  await expect(page.getByRole('region', { name: '实际成片播放' })).toHaveAttribute('data-playback-state', 'ready');
  await page.screenshot({ path: info.outputPath('frozen-result-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.assembly-result-screen')).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath('frozen-result-mobile.png'), fullPage: true, animations: 'disabled' });
});
