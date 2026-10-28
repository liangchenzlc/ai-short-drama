import { expect, test } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

for (const size of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`workbench navigation, story settings and dual resource views ${size.width}`, async ({ page }, info) => {
    await page.setViewportSize(size);
    const data = await fixture(page, true);
    await page.goto(`${root}/source`);
    const nav = page.getByRole('navigation', { name: '创作流程' });
    await expect(nav).toHaveCSS('width', '40px');
    await expect(nav).toHaveCSS('height', `${size.height}px`);
    await expect(nav.getByRole('button')).toHaveCount(4);
    await nav.getByRole('button', { name: '剧本创作', exact: true }).focus();
    await expect(page.getByRole('tooltip', { name: '剧本创作', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '剧本', exact: true })).toBeVisible();
    await expect(page.locator('.sd-doc-foot')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /导入剧本|导出剧本/ })).toHaveCount(0);
    await page.getByRole('tab', { name: '故事设定', exact: true }).click();
    for (const [label, text] of [['一句话卖点', '她发现每次重拍都会改变现实。'], ['故事梗概', '一名剪辑师试图找回被改写的记忆。'], ['故事类型', '悬疑'], ['人物小传', '林晚：克制，执着于真相。']]) await page.getByRole('textbox', { name: label, exact: true }).fill(text);
    await expect.poll(() => data.requests.filter(request => request.path.endsWith('/story-settings') && request.method === 'PUT').at(-1)?.body.character_bios).toBe('林晚：克制，执着于真相。');
    const field = page.getByRole('textbox', { name: '故事梗概', exact: true });
    await field.focus();
    await expect(field).toHaveCSS('border-top-width', '0px');
    await expect(field).toHaveCSS('box-shadow', 'none');
    await page.screenshot({ path: info.outputPath(`story-settings-${size.width}.png`), animations: 'disabled' });
    await page.reload();
    await expect(page.getByRole('textbox', { name: '故事类型', exact: true })).toHaveValue('悬疑');
    await page.getByRole('tab', { name: '故事设定', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: '剧本正文', exact: true })).toBeFocused();
    const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
    await editor.fill('新的剧本正文。');
    await expect.poll(() => data.writing.editing_script.content).toBe('新的剧本正文。');
    await editor.press('Control+z');
    await expect(editor).toHaveValue('林晚走进车站，抬头寻找站台。');
    await page.screenshot({ path: info.outputPath(`script-${size.width}.png`), animations: 'disabled' });
    for (const [stage, title, panelSelector, rowSelector, createLabel] of [
      ['资产生成', '资产库', '.standard-assets-list', '.standard-asset-card', '创建资产'],
      ['分镜制作', '分镜表', '.standard-storyboard-list', '.storyboard-shot-card', '创建分镜'],
    ]) {
      await nav.getByRole('button', { name: stage, exact: true }).click();
      const panel = page.locator(panelSelector);
      await expect(panel.getByRole('heading', { name: new RegExp(title) })).toBeVisible();
      await panel.getByRole('button', { name: '列表展示', exact: true }).click();
      await expect(panel.locator(rowSelector).first()).toHaveCSS('height', '45px');
      const create = panel.getByRole('button', { name: createLabel, exact: true });
      await expect(create).toHaveCSS('height', '45px');
      await expect(create).toBeInViewport();
      await expect(panel.getByRole('button', { name: /从剧本提取|^提取分镜$|^重新提取$/ })).toHaveCount(0);
      await panel.locator(rowSelector).first().hover();
      await expect(panel.getByRole('button', { name: /更多操作/ }).first()).toHaveCSS('opacity', '1');
      await page.screenshot({ path: info.outputPath(`${stage}-list-${size.width}.png`), animations: 'disabled' });
      await panel.getByRole('button', { name: '宫格展示', exact: true }).click();
      await page.screenshot({ path: info.outputPath(`${stage}-grid-${size.width}.png`), animations: 'disabled' });
      await panel.getByRole('button', { name: '列表展示', exact: true }).click();
      await page.reload();
      await expect(page.locator(panelSelector).getByRole('button', { name: '列表展示', exact: true })).toHaveAttribute('aria-pressed', 'true');
      if (stage === '分镜制作') {
        await page.getByRole('button', { name: createLabel, exact: true }).click();
        await expect.poll(() => data.shots.length).toBe(81);
        await expect(page.getByRole('textbox', { name: '镜头名称', exact: true })).toHaveValue('');
      } else {
        await page.getByRole('button', { name: createLabel, exact: true }).click();
        await expect(page.getByRole('textbox', { name: '素材名称', exact: true })).toHaveValue('');
      }
    }
    await nav.getByRole('button', { name: '成片预览', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${root}/assembly`));
    await expect(page.getByRole('button', { name: 'AI 创作助手', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}

test('story settings retain local input on conflict until explicit reconciliation', async ({ page }) => {
  const data = await fixture(page, true);
  let server = { row_version: '0', logline: '', synopsis: '', genre: '', character_bios: '' };
  const writes: typeof server[] = [];
  let conflict = true;
  await page.route('**/api/v1/projects/10/story-settings', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: server });
    const body: typeof server = route.request().postDataJSON(); writes.push(body);
    if (conflict) {
      conflict = false; server = { ...server, row_version: '1', logline: '另一窗口的设定' };
      return route.fulfill({ status: 409, json: { error: { code: 'story_settings_version_conflict', message: '故事设定已变化' } } });
    }
    expect(body.row_version).toBe(server.row_version);
    server = { ...body, row_version: String(BigInt(server.row_version) + 1n) };
    return route.fulfill({ json: server });
  });
  await page.goto(`${root}/source?writing=settings`);
  const logline = page.getByRole('textbox', { name: '一句话卖点', exact: true });
  await logline.fill('本机保留的卖点');
  const reconcile = page.getByRole('button', { name: '核对后保留本机设定', exact: true });
  await expect.poll(() => writes.length).toBe(1);
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await expect(reconcile).toBeVisible();
  await expect(logline).toHaveValue('本机保留的卖点');
  expect(writes).toHaveLength(1);
  expect(server.logline).toBe('另一窗口的设定');
  await reconcile.click();
  await expect.poll(() => server.logline).toBe('本机保留的卖点');
  await page.reload();
  await expect(logline).toHaveValue('本机保留的卖点');
  expect(server.row_version).toBe('2');
  expect(data.errors).toEqual([]);
});

test('story settings defer Chinese composition and restore unsent input after reload', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page, true);
  await page.goto(`${root}/source?writing=settings`);
  const synopsis = page.getByRole('textbox', { name: '故事梗概', exact: true });
  await synopsis.focus(); await synopsis.dispatchEvent('compositionstart', { data: '' });
  await synopsis.fill('合成中的中文梗概');
  await page.clock.runFor(1500);
  expect(data.requests.filter(request => request.path.endsWith('/story-settings') && request.method === 'PUT')).toHaveLength(0);
  await synopsis.dispatchEvent('compositionend', { data: '合成中的中文梗概' });
  await page.reload();
  await expect(synopsis).toHaveValue('合成中的中文梗概');
  await page.clock.runFor(1500);
  await expect.poll(() => data.requests.filter(request => request.path.endsWith('/story-settings') && request.method === 'PUT').at(-1)?.body.synopsis).toBe('合成中的中文梗概');
  expect(data.errors).toEqual([]);
});
