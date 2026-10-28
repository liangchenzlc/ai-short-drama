import { mkdir } from 'node:fs/promises';
import { expect, type Page } from '@playwright/test';

export const refinementViewports = [
  { width: 1440, height: 1000 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
];

export async function captureRefinement(page: Page, name: string, width: number) {
  await mkdir('.runtime/refinement-visual', { recursive: true });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  // 全页截图从页面起点采集，避免之前滚动后的 sticky 控件改变截图偏移。
  await page.evaluate(async () => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    window.scrollTo({ top: 0, behavior: 'instant' });
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await page.screenshot({ path: `.runtime/refinement-visual/${name}-${width}.png`, fullPage: true, animations: 'disabled' });
}
