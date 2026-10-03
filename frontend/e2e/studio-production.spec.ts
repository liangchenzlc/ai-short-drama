import { writeFileSync, mkdirSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const productionOrigin = process.env.PLAYWRIGHT_PRODUCTION_URL;
test.use({ baseURL: productionOrigin ?? 'http://127.0.0.1:4175' });

test('production routes render with deferred editor bundles and compact controls', async ({ page }) => {
  test.skip(!productionOrigin, 'Run against vite preview with PLAYWRIGHT_PRODUCTION_URL.');
  test.setTimeout(60000);
  const state = await fixture(page, true, productionOrigin);
  const assets: string[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.endsWith('.js')) assets.push(url.pathname);
  });
  await page.goto('/projects');
  await expect(page.locator('.project-tile')).toHaveCount(4);
  await expect(page.getByRole('button', { name: '刷新', exact: true })).toHaveCount(0);
  expect(assets.filter(url => /timeline-|data-table-|drawers-/.test(url))).toEqual([]);
  const firstRouteAssets = [...assets];
  const firstRouteTiming = await page.evaluate(() => ({
    paints: performance.getEntriesByType('paint').map(entry => ({ name: entry.name, startTime: entry.startTime })),
    resources: performance.getEntriesByType('resource').filter(entry => /\.(js|css)$/.test(entry.name)).map(entry => ({ name: entry.name, duration: entry.duration })),
    note: 'Local production build with intercepted API fixtures; timings are not field performance.',
  }));
  const controls = await page.locator('.studio-page-head button, .project-search .ant-input-affix-wrapper').evaluateAll(nodes => nodes.map(node => ({
    text: node.textContent?.trim() || node.getAttribute('aria-label'),
    height: node.getBoundingClientRect().height,
    fontSize: getComputedStyle(node).fontSize,
    color: getComputedStyle(node).color,
    background: getComputedStyle(node).backgroundColor,
  })));
  let configControls: { height: number; tag: string; label: string; className: string; fontSize: string; padding: string }[] = [];
  const nativePrimaryContrast: { state: string; ratio: number }[] = [];
  const directory = '.runtime/review/production';
  mkdirSync(directory, { recursive: true });
  writeFileSync(`${directory}/performance.json`, JSON.stringify({ firstRouteAssets, controls, firstRouteTiming }, null, 2));
  expect(controls.every(control => control.height >= 28 && control.height <= 38)).toBe(true);
  await page.screenshot({ path: `${directory}/projects-1440.png`, fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '新建项目', exact: true }).getByLabel('项目名称')).toBeFocused();
  await page.keyboard.press('Escape');

  for (const route of ['/ai_config', '/assets/character', '/tasks/image', '/media-library/image', `${root}/storyboard`, `${root}/assembly`]) {
    await page.goto(route);
    await expect(page.locator('h1'), `Production route: ${route}`).toBeVisible();
    if (route === '/ai_config') {
      await page.getByRole('button', { name: '添加文本模型' }).click();
      const config = page.getByRole('dialog', { name: /添加|新建/ });
      await expect(config).toBeVisible();
      configControls = await config.locator('input:not([type="checkbox"]), select, .ant-select-selector').evaluateAll(nodes => nodes.filter(node => node.getBoundingClientRect().height > 0
        && !(node.tagName === 'INPUT' && node.closest('.ant-select'))).map(node => ({
        height: node.getBoundingClientRect().height,
        tag: node.tagName,
        label: node.closest('label')?.firstChild?.textContent?.trim() || node.getAttribute('id') || '',
        className: node.className,
        fontSize: getComputedStyle(node).fontSize,
        padding: getComputedStyle(node).padding,
      })));
      writeFileSync(`${directory}/config-controls.json`, JSON.stringify(configControls, null, 2));
      expect(configControls.filter(control => control.height < 28 || control.height > 38)).toEqual([]);
      const nativePrimary = config.locator('.studio-primary');
      for (const state of ['rest', 'hover']) {
        if (state === 'hover') await nativePrimary.hover();
        const ratio = await nativePrimary.evaluate(node => {
          const style = getComputedStyle(node);
          const luminance = (color: string) => {
            const values = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(value => value / 255);
            return values.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
          };
          const values = [luminance(style.color), luminance(style.backgroundColor)].sort((a, b) => a - b);
          return (values[1] + .05) / (values[0] + .05);
        });
        expect(ratio).toBeGreaterThanOrEqual(4.5);
        nativePrimaryContrast.push({ state, ratio });
      }
      await page.screenshot({ path: `${directory}/config-form-1440.png`, animations: 'disabled' });
      await page.keyboard.press('Escape');
    }
    if (route.endsWith('/storyboard')) await expect(page.locator('.storyboard-summary')).toHaveCount(20);
    if (route.endsWith('/assembly')) await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
  expect(assets.some(url => /timeline-/.test(url))).toBe(true);
  expect(assets.some(url => /data-table-/.test(url))).toBe(true);
  await page.screenshot({ path: `${directory}/assembly-1440.png`, fullPage: true, animations: 'disabled' });
  writeFileSync(`${directory}/performance.json`, JSON.stringify({ firstRouteAssets, controls, configControls, nativePrimaryContrast, firstRouteTiming, allRouteAssets: [...new Set(assets)], errors: state.errors, unexpected: state.unexpected }, null, 2));
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
