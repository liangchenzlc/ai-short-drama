import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

async function openRecovery(page: Page) {
  const status = page.getByRole('button', { name: /^保存状态：/ });
  if (await status.getAttribute('aria-expanded') !== 'true') await status.click();
  const review = page.locator('.standard-writing-review');
  if (!await review.evaluate(node => (node as HTMLDetailsElement).open)) {
    await review.getByText('剧本版本核对', { exact: true }).click();
  }
}
const scope = 'creation-recovery:user:anonymous:writing:10:20';

test('same-base writing recovery resumes automatically without confirmation', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const novel = page.getByRole('textbox', { name: '本集小说正文', exact: true });
  await expect(novel).toBeEditable(); await novel.fill('同版本自动恢复的正文');
  await page.reload();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(novel).toHaveValue('同版本自动恢复的正文'); await expect(novel).toBeEditable();
  await expect(page.getByRole('dialog', { name: /确认|未保存/ })).toHaveCount(0);
  await page.clock.runFor(1000);
  await expect.poll(() => data.writing.novel.content).toBe('同版本自动恢复的正文');
  expect(data.requests.filter(r => r.path === `${root}/novel` && r.method === 'PUT')).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('changed-base writing recovery is available in nonmodal save details', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const novel = page.getByRole('textbox', { name: '本集小说正文', exact: true });
  await expect(novel).toBeEditable(); await novel.fill('刷新前的完整正文恢复稿');
  data.writing.content_version = '9007199254740993'; data.writing.novel.content = '另一窗口的最新正文';
  await page.reload();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(novel).toHaveValue('另一窗口的最新正文'); await expect(novel).not.toBeEditable();
  await page.getByRole('button', { name: '返回剧本', exact: true }).click();
  await openRecovery(page);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整恢复记录', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('episode-20-recovery.json');
  await page.getByRole('button', { name: '保留本机内容继续编辑', exact: true }).click();
  await expect(page.getByRole('dialog', { name: /确认|未保存/ })).toHaveCount(0);
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(novel).toHaveValue('刷新前的完整正文恢复稿'); await expect(novel).toBeEditable();
  await novel.fill('核对后继续编辑'); await page.clock.runFor(1000);
  await expect.poll(() => data.requests.filter(r => r.path === `${root}/novel` && r.method === 'PUT').length).toBe(1);
  expect(data.requests.find(r => r.path === `${root}/novel` && r.method === 'PUT')!.body.content_version).toBe('9007199254740993');
  expect(data.errors).toEqual([]);
});

test('damaged recovery can be backed up and cleared once without another confirmation', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.addInitScript(({ key }) => localStorage.setItem(key, '{damaged-record'), { key: scope });
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const novel = page.getByRole('textbox', { name: '本集小说正文', exact: true });
  await expect(novel).toHaveValue(data.writing.novel.content); await expect(novel).not.toBeEditable();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBe('{damaged-record');
  await page.getByRole('button', { name: '返回剧本', exact: true }).click();
  await openRecovery(page);
  await page.getByRole('button', { name: '保留服务端内容并清除损坏记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: /确认|未保存/ })).toHaveCount(0);
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('creation-recovery:user:anonymous:writing-before-replace:10:20'))).toContain('{damaged-record');
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(novel).toBeEditable();
  expect(data.requests.filter(r => r.path === `${root}/novel` && r.method === 'PUT')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test('failed recovery check retains original record and allows stage switching', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const novel = page.getByRole('textbox', { name: '本集小说正文', exact: true });
  await expect(novel).toBeEditable(); await novel.fill('读取失败时必须保留的正文恢复稿');
  const record = await page.evaluate(key => localStorage.getItem(key), scope);
  data.writing.content_version = '2'; data.writing.novel.content = '另一个窗口修改后的正文';
  await page.reload(); await openRecovery(page);
  let failing = true;
  await page.route(`**/api/v1${root}/writing`, async route => failing
    ? route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '正文读取暂不可用' } } }) : route.fallback());
  await page.getByRole('button', { name: '保留本机内容继续编辑', exact: true }).click();
  await expect(page.getByRole('group').getByText('恢复前读取最新正文失败，恢复稿仍保留，请重试。', { exact: true })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBe(record);
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.getByRole('button', { name: '素材生成', exact: true }).click();
  await expect(page).toHaveURL(/\/assets/);
  await expect(page.getByRole('dialog', { name: /确认|未保存/ })).toHaveCount(0);
  await page.getByRole('button', { name: '剧本创作', exact: true }).click();
  failing = false; await openRecovery(page);
  await page.getByRole('button', { name: '保留本机内容继续编辑', exact: true }).click();
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(novel).toHaveValue('读取失败时必须保留的正文恢复稿'); await expect(novel).toBeEditable();
  expect(data.errors).toEqual([]);
});

test('explicit server replacement preserves a conflicting local draft until a retry reads the server', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  let writes = 0, failingReads = false;
  await page.route(`**/api/v1${root}/novel`, async route => {
    writes++;
    return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT' } } });
  });
  await page.route(`**/api/v1${root}/writing`, async route => failingReads
    ? route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } }) : route.fallback());
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const novel = page.getByRole('textbox', { name: '本集小说正文', exact: true });
  await novel.fill('显式替换前必须保留的冲突稿');
  await expect.poll(() => writes).toBe(1);
  await expect(page.getByRole('button', { name: /^保存状态：需处理/ })).toBeVisible();
  const originalRecord = await page.evaluate(key => localStorage.getItem(key), scope);
  await page.getByRole('button', { name: '返回剧本', exact: true }).click();
  await openRecovery(page);
  const review = page.locator('.standard-writing-review');
  failingReads = true;
  await review.getByRole('button', { name: '使用服务端版本', exact: true }).click();
  await expect(review.getByText('读取服务端正文失败，本机内容和恢复记录仍保留，请重试。', { exact: true })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBe(originalRecord);
  expect(await page.evaluate(() => localStorage.getItem('creation-recovery:user:anonymous:writing-before-replace:10:20'))).toContain('显式替换前必须保留的冲突稿');
  data.writing.content_version = '2'; data.writing.novel.content = '重试后读取到的最新服务端正文';
  failingReads = false;
  await review.getByRole('button', { name: '使用服务端版本', exact: true }).click();
  await expect(page.getByRole('button', { name: '保存状态：已保存', exact: true })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBeNull();
  await expect(page.getByRole('dialog', { name: /确认|未保存/ })).toHaveCount(0);
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(novel).toHaveValue('重试后读取到的最新服务端正文'); await expect(novel).toBeEditable();
  expect(writes).toBe(1); expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
