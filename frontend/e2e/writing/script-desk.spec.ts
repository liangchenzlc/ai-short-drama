import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

const script = '创作备注：保留这一行。\n\n场 01 · 夜 · 内 · 医院走廊\n人物：林晚、陈医生\n\n林晚把报告放在桌上。\n林晚：报告，是谁改的？\n\n场 02 · 夜 · 外 · 医院门口\n\n林晚推开玻璃门。\n林晚：我会查清楚。\n';
async function setup(page: Page, text = script) {
  const data = await fixture(page, true);
  data.writing.editing_script.content = text;
  await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toHaveValue(text);
  return data;
}

test('one continuous document saves directly with story tabs', async ({ page }) => {
  const data = await setup(page);
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await expect(page.locator('.sd-input')).toHaveCount(1);
  await expect(page.locator('.sd-doc-head').getByRole('button')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: '创作目录' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /生成剧本|生成大纲|小说原文|故事大纲|打开创作目录|进入素材提取|让助手起草剧本|最近变更/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'AI 改写', exact: true })).toHaveCount(0);
  const changed = script.replace('医院走廊', '急诊走廊');
  await editor.fill(changed);
  await expect.poll(() => data.writing.editing_script.content).toBe(changed);
  expect(data.writing.confirmed_script_id).toBe('40');
  await page.reload(); await expect(editor).toHaveValue(changed);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('long document retains scroll and undo history through stage and assistant changes', async ({ page }) => {
  const long = Array.from({ length: 20 }, (_, index) => `场 ${index + 1} · 夜 · 内 · 走廊\n${'林晚沿着走廊寻找报告。'.repeat(12)}\n\n`).join('');
  const data = await setup(page, long);
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.fill(long + '新增结尾');
  await expect.poll(() => data.writing.editing_script.content).toBe(long + '新增结尾');
  const scroll = page.locator('.sd-scroll');
  await scroll.evaluate(node => { node.scrollTop = node.scrollHeight; });
  const position = await scroll.evaluate(node => node.scrollTop);
  expect(position).toBeGreaterThan(1000);
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '剧本创作', exact: true }).click();
  await expect.poll(() => scroll.evaluate(node => node.scrollTop)).toBeCloseTo(position, 0);
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  await expect(editor).toHaveValue(long + '新增结尾');
  await editor.press('Control+z');
  await expect(editor).toHaveValue(long);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('empty scripts accept plain text and explain selection without a generation entry', async ({ page }) => {
  const data = await setup(page, '');
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await expect(page.getByRole('button', { name: '导出剧本', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /让助手起草剧本|最近变更/ })).toHaveCount(0);
  await expect(editor).toHaveAttribute('placeholder', '在这里写剧本，也可以让助手起草。');
  await editor.focus(); await editor.press('Control+k');
  await expect(page.locator('.sd-editor-hint')).toHaveText('先选中需要改写的文字');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await editor.fill('未分场的文字也必须完整保存。');
  await expect(page.locator('.sd-highlight')).toHaveText('未分场的文字也必须完整保存。');
  await expect.poll(() => data.writing.editing_script.content).toBe('未分场的文字也必须完整保存。');
});

test('Chinese composition waits until completion and preserves a single undo transaction', async ({ page }) => {
  const data = await setup(page);
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.focus(); await editor.dispatchEvent('compositionstart', { data: '' });
  await editor.fill('中文输入期间的完整剧本。');
  await page.waitForTimeout(1100);
  expect(data.requests.filter(request => request.path === `${root}/script`)).toHaveLength(0);
  await expect(page.getByRole('button', { name: 'AI 改写', exact: true })).toHaveCount(0);
  await editor.dispatchEvent('compositionend', { data: '中文输入期间的完整剧本。' });
  await expect.poll(() => data.writing.editing_script.content).toBe('中文输入期间的完整剧本。');
  await expect(editor).toBeFocused();
  await editor.press('Control+z');
  await expect(editor).toHaveValue(script);
  expect(data.errors).toEqual([]);
});

test('near-limit scripts keep complete text, editable caret and aligned rendering', async ({ page }) => {
  test.setTimeout(60000);
  const paragraph = '林晚沿着走廊寻找报告。'.repeat(30) + '\n\n';
  const long = paragraph.repeat(Math.floor(1047000 / Buffer.byteLength(paragraph)));
  const data = await setup(page, long);
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.focus(); await editor.press('Control+End');
  await page.keyboard.insertText('新');
  await expect.poll(() => data.writing.editing_script.content.endsWith('新')).toBe(true);
  expect(await editor.evaluate(node => (node as HTMLTextAreaElement).value.length)).toBe(long.length + 1);
  const geometry = await page.locator('.sd-body').evaluate(node => {
    const display = node.querySelector('.sd-highlight')!.getBoundingClientRect();
    const input = node.querySelector('.sd-input')!.getBoundingClientRect();
    return { width: display.width - input.width, height: display.height - input.height };
  });
  expect(Math.abs(geometry.width)).toBeLessThan(1);
  expect(Math.abs(geometry.height)).toBeLessThan(1);
  expect(data.errors).toEqual([]);
});

for (const width of [1440, 1920, 1366, 1280]) test(`single document visual and geometry ${width}`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: width === 1280 ? 720 : 1000 });
  const data = await setup(page);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeInViewport();
  await expect(page.locator('.sd-input')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('.sd-input')).toHaveCSS('font-size', '16px');
  await expect(page.locator('.sd-doc')).toHaveCSS('background-color', 'rgb(24, 24, 24)');
  await expect(page.locator('.sd-desk')).toHaveCSS('background-color', 'rgb(24, 24, 24)');
  const geometry = await page.locator('.sd-desk').evaluate(node => {
    const desk = node.getBoundingClientRect(), paper = node.querySelector('.sd-doc')!.getBoundingClientRect();
    return { width: paper.width, offset: (paper.left + paper.right - desk.left - desk.right) / 2 };
  });
  expect(geometry.width).toBeLessThanOrEqual(960);
  expect(Math.abs(geometry.offset)).toBeLessThan(1);
  await expect(page.locator('.sd-doc-head').getByRole('button')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath(`writing-${width}.png`), animations: 'disabled' });
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
