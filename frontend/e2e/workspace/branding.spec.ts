import { readFileSync } from 'node:fs';
import { expect, test, type Locator, type Page } from '@playwright/test';
import type { BrandConfig } from '../../src/types/shared/branding';
import { fixture } from '../support/studio-fixture';

function configuredBrand(): BrandConfig {
  return JSON.parse(readFileSync(new URL('../../../backend/src/short_drama/core/branding/brand.json', import.meta.url), 'utf8'));
}

async function imageFits(image: Locator) {
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0)).toBe(true);
  const geometry = await image.evaluate((node: HTMLImageElement) => {
    const imageRect = node.getBoundingClientRect();
    const parentRect = node.parentElement!.getBoundingClientRect();
    return {
      width: imageRect.width,
      height: imageRect.height,
      ratio: imageRect.width / imageRect.height,
      naturalRatio: node.naturalWidth / node.naturalHeight,
      inside: imageRect.left >= parentRect.left && imageRect.right <= parentRect.right + 1,
    };
  });
  expect(geometry.ratio, JSON.stringify(geometry)).toBeCloseTo(geometry.naturalRatio, 2);
  expect(geometry.inside).toBe(true);
}

async function brandMetadata(page: Page) {
  const brand = configuredBrand();
  await expect(page).toHaveTitle(brand.titles.app.replaceAll('{name}', brand.name));
  await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', brand.descriptions.app.replaceAll('{name}', brand.name));
  const iconUrl = await page.locator('link[rel="icon"]').getAttribute('href');
  expect(iconUrl).toBeTruthy();
  const icon = await page.request.get(new URL(iconUrl!, page.url()).href);
  expect(icon.ok()).toBe(true);
  expect(icon.headers()['content-type']).toMatch(/^image\//);
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`configured brand stays readable and reachable at ${viewport.width}px`, async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize(viewport);
    await page.goto('/projects');
    const brand = configuredBrand();
    const home = page.getByRole('link', { name: `${brand.name} 首页`, exact: true });
    await expect(home).toBeVisible();
    await expect(home).toContainText(brand.name);
    await expect(home).toContainText(brand.tagline);
    await imageFits(home.locator('img'));
    await home.focus();
    await expect(home).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/projects$/);
    await brandMetadata(page);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath('sidebar.png'), animations: 'disabled' });

    for (const path of ['/login', '/register', '/verify-email', '/reset-password']) {
      await page.goto(path);
      const identityBrand = page.locator('.identity-brand');
      await expect(identityBrand).toHaveAccessibleName(`${brand.name} 首页`);
      await imageFits(identityBrand.getByRole('img', { name: brand.name, exact: true }));
      await brandMetadata(page);
      await identityBrand.focus();
      await expect(identityBrand).toBeFocused();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await page.screenshot({ path: info.outputPath(`${path.slice(1)}.png`), animations: 'disabled' });
    }
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}
