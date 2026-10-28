import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';
import type { TextGenerationRequest } from '../../src/api/types/generations';

async function setup(page: Page) {
  const data = await fixture(page, true);
  const initial = '😀开头\n同一句话\n转场\n同一句话\n结尾';
  data.writing.editing_script.content = initial;
  const submissions: { body: TextGenerationRequest; key: string }[] = [];
  const controls = { complete: false, loseResponse: false, received: false, status: 'running' };
  let applied = false;
  const receipt = { generation_id: '99001', service_type: 'text', status: 'queued' };
  await page.route('**/api/v1/ai/generations/text', async route => {
    const body = route.request().postDataJSON() as TextGenerationRequest;
    submissions.push({ body, key: route.request().headers()['idempotency-key'] });
    controls.received = true;
    data.writing.active_writing_tasks = [{ task_id: '99001', target: 'script', operation: 'script_rewrite', status: 'running' }];
    if (controls.loseResponse) return route.abort();
    return route.fulfill({ json: receipt });
  });
  await page.route('**/api/v1/ai/generations/by-idempotency-key**', route => route.fulfill({ json: controls.received ? receipt : null }));
  await page.route('**/api/v1/ai/generations/99001/cancel', route => {
    controls.status = 'cancelled'; data.writing.active_writing_tasks = [];
    return route.fulfill({ json: { ...receipt, status: 'cancelled' } });
  });
  await page.route('**/api/v1/ai/generations/99001', async route => {
    const source = submissions[0]?.body.source;
    if (controls.complete && !applied && source?.scene === 'script_rewrite') {
      applied = true;
      data.writing.editing_script.content = Array.from(initial).slice(0, source.start).join('') + '新的台词' + Array.from(initial).slice(source.end).join('');
      data.writing.editing_script.state = 'confirmed'; data.writing.confirmed_script_id = '40';
      data.writing.content_version = String(BigInt(data.writing.content_version) + 1n);
      data.writing.active_writing_tasks = [];
    }
    return route.fulfill({ json: { ...receipt, source, status: controls.complete ? 'succeeded' : controls.status, created_at: '2026-10-27T00:00:00Z', can_cancel: true, can_resume: false, can_retry: false, error: null, input: {}, parameters: {}, result: { text: null, assets: [], partial: false } } });
  });
  await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await expect(editor).toHaveValue(initial);
  return { ...data, initial, submissions, controls, editor };
}

async function openRewrite(page: Page) {
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.focus();
  await editor.evaluate(node => {
    const input = node as HTMLTextAreaElement;
    const start = input.value.lastIndexOf('同一句话'); input.setSelectionRange(start, start + 4);
  });
  await editor.press('Control+k');
  const dialog = page.getByRole('dialog', { name: 'AI 改写', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('textbox', { name: '修改要求' }).fill('改得更有张力');
  return dialog;
}

test('rewrite freezes exact Unicode range, preserves footer geometry and is one visible undo transaction', async ({ page }) => {
  const data = await setup(page);
  const before = await page.locator('.sd-scroll').boundingBox();
  const dialog = await openRewrite(page);
  await expect(page.locator('.sd-range-highlight')).toHaveText('同一句话');
  await expect(dialog.getByText('已选 4 字', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('combobox', { name: '文本模型' })).toBeVisible();
  await dialog.getByRole('button', { name: '改写选区', exact: true }).click();
  await expect.poll(() => data.submissions.length).toBe(1);
  const source = data.submissions[0].body.source;
  if (source?.scene !== 'script_rewrite') throw new Error('Expected rewrite source');
  const start = data.initial.lastIndexOf('同一句话');
  expect(source.start).toBe(Array.from(data.initial.slice(0, start)).length);
  expect(source.selected_text).toBe('同一句话');
  expect(data.submissions[0].body.config_id).toBe('77');
  await expect(data.editor).not.toBeEditable();
  await expect(page.getByRole('button', { name: '导入剧本', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '导出剧本', exact: true })).toBeEnabled();
  await expect(page.locator('.sd-foot-status')).toContainText('正在改写选区');
  expect((await page.locator('.sd-scroll').boundingBox())?.height).toBe(before?.height);
  data.controls.complete = true;
  const changed = data.initial.slice(0, start) + '新的台词' + data.initial.slice(start + 4);
  await expect(data.editor).toHaveValue(changed); await expect(data.editor).toBeEditable();
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(data.editor).toHaveValue(data.initial);
  await expect.poll(() => data.writing.editing_script.content).toBe(data.initial);
  await page.getByRole('button', { name: '重做', exact: true }).click();
  await expect(data.editor).toHaveValue(changed);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('closing restores selection and retains instructions without submitting', async ({ page }) => {
  const data = await setup(page);
  const dialog = await openRewrite(page);
  await expect(dialog.getByRole('textbox', { name: '修改要求' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0); await expect(data.editor).toBeFocused();
  expect(await data.editor.evaluate(node => {
    const input = node as HTMLTextAreaElement; return input.value.slice(input.selectionStart, input.selectionEnd);
  })).toBe('同一句话');
  await data.editor.press('Control+k');
  await expect(dialog.getByRole('textbox', { name: '修改要求' })).toHaveValue('改得更有张力');
  await page.mouse.click(20, 400);
  await expect(dialog).toHaveCount(0);
  expect(data.submissions).toHaveLength(0);
});

test('rewrite continues after leaving the stage and does not apply twice on return', async ({ page }) => {
  const data = await setup(page);
  const dialog = await openRewrite(page);
  await dialog.getByRole('button', { name: '改写选区', exact: true }).click();
  await expect.poll(() => data.submissions.length).toBe(1);
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  data.controls.complete = true;
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '剧本创作', exact: true }).click();
  await expect(data.editor).toHaveValue(data.initial.replace(/同一句话\n结尾$/, '新的台词\n结尾'));
  expect(data.submissions).toHaveLength(1);
});

test('stopping releases the document only after confirmation and preserves the original', async ({ page }) => {
  const data = await setup(page);
  const dialog = await openRewrite(page);
  await dialog.getByRole('button', { name: '改写选区', exact: true }).click();
  await expect(data.editor).not.toBeEditable();
  await page.getByRole('button', { name: '停止', exact: true }).click();
  await expect(data.editor).toBeEditable(); await expect(data.editor).toHaveValue(data.initial);
  expect(data.submissions).toHaveLength(1);
});

test('save conflict blocks model submission and keeps original selection and instructions', async ({ page }) => {
  const data = await setup(page);
  await page.route(`**/api/v1${root}/script`, route => route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT' } } }));
  await data.editor.fill(data.initial + '\n尚未保存');
  const dialog = await openRewrite(page);
  await dialog.getByRole('button', { name: '改写选区', exact: true }).click();
  await dialog.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(dialog.getByText('正文尚未保存成功，请先处理保存提示。', { exact: true }).first()).toBeVisible();
  await expect(dialog.getByRole('textbox', { name: '修改要求' })).toHaveValue('改得更有张力');
  await expect(page.locator('.sd-range-highlight')).toHaveText('同一句话');
  expect(data.submissions).toHaveLength(0);
});

test('damaged request recovery stays reachable and blocks new rewrites until checked', async ({ page }) => {
  const data = await setup(page);
  const key = 'creation-recovery:user:anonymous:writing-generation:10';
  await page.evaluate(key => localStorage.setItem(key, '{damaged-writing-request'), key);
  await page.reload();
  await data.editor.focus();
  await data.editor.evaluate(node => (node as HTMLTextAreaElement).setSelectionRange(0, 3));
  await data.editor.press('Control+k');
  await expect(page.getByRole('dialog', { name: 'AI 改写', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载原请求记录', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('writing-request-10.json');
  await page.getByRole('button', { name: '已核对，清理记录', exact: true }).click();
  await page.getByRole('dialog', { name: '清理损坏的请求记录', exact: true }).getByRole('button', { name: '已核对，清理记录', exact: true }).click();
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBeNull();
  expect(await page.evaluate(key => localStorage.getItem(`${key}:previous`), key)).toContain('{damaged-writing-request');
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await openRewrite(page);
  await expect(data.editor).toHaveValue(data.initial);
});

test('lost submission response restores the same request without duplicate generation', async ({ page }) => {
  const data = await setup(page); data.controls.loseResponse = true;
  const dialog = await openRewrite(page);
  await dialog.getByRole('button', { name: '改写选区', exact: true }).click();
  await expect.poll(() => data.submissions.length).toBe(1);
  await expect(dialog.getByRole('button', { name: '改写选区', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.locator('.sd-foot-status')).toContainText('改写请求待核对');
  await page.reload();
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.getByRole('button', { name: '核对原任务', exact: true }).click();
  data.controls.complete = true;
  await expect(data.editor).toHaveValue(data.initial.replace(/同一句话\n结尾$/, '新的台词\n结尾'));
  expect(data.submissions).toHaveLength(1);
});

for (const width of [1440, 1280]) test(`selection and rewrite visual geometry ${width}`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: width === 1280 ? 720 : 1000 });
  const data = await setup(page);
  const dialog = await openRewrite(page);
  await expect(page.locator('.sd-range-highlight')).toHaveText('同一句话');
  const bounds = await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
  expect(bounds!.y).toBeGreaterThanOrEqual(0); expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(width === 1280 ? 720 : 1000);
  expect(bounds!.width).toBe(360);
  await page.screenshot({ path: info.outputPath(`rewrite-${width}.png`), animations: 'disabled' });
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
