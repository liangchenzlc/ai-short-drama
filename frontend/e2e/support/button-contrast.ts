import { writeFile } from 'node:fs/promises';
import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';

export async function checkPrimaryButtonContrast(page: Page, button: Locator, info: TestInfo, name: string) {
  const samples: Record<string, { foreground: string; background: string; ratio: number }> = {};
  async function check(state: string) {
    await button.evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {}))); });
    const sample = () => button.evaluate(node => {
      const style = getComputedStyle(node);
      function luminance(color: string) {
        const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(value => {
          const channel = Number(value) / 255;
          return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
        });
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
      }
      const foreground = luminance(style.color), background = luminance(style.backgroundColor);
      return { foreground: style.color, background: style.backgroundColor, ratio: (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05) };
    });
    await expect.poll(async () => (await sample()).ratio, { message: `${name} ${state} text contrast` }).toBeGreaterThanOrEqual(4.5);
    samples[state] = await sample();
  }

  await page.mouse.move(0, 0);
  await button.evaluate(node => (node as HTMLElement).blur());
  await check('default');
  await button.hover();
  await check('hover');
  await page.mouse.move(0, 0);
  await button.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(button).toBeFocused();
  expect(await button.evaluate(node => node.matches(':focus-visible'))).toBe(true);
  await check('keyboard-focus');
  await button.hover();
  await page.mouse.down();
  try {
    expect(await button.evaluate(node => node.matches(':active'))).toBe(true);
    await check('active');
  } finally {
    const box = (await button.boundingBox())!;
    await page.mouse.move(box.x - 4, box.y + box.height / 2);
    await page.mouse.up();
  }
  await button.hover();
  await writeFile(info.outputPath(`${name}-contrast.json`), JSON.stringify(samples, null, 2));
}
