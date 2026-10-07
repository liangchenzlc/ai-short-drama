import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function layoutFixture(page: Page) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  state.shots[0].script = '中景，林晚停在雨夜中的站台。'.repeat(16);
  state.shots[1].image.is_stale = true;
  return state;
}

test('cards show adopted frames, two-line scripts and separate media states', async ({ page }, info) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  const cards = page.locator('.storyboard-shot-card');
  await expect(cards).toHaveCount(20);
  await expect(cards.first().locator('.storyboard-card-media img')).toBeVisible();
  await expect(cards.nth(3).getByText('尚无采用画面', { exact: true })).toBeVisible();
  await expect(cards.nth(1).getByText('图片：需核对', { exact: true })).toBeVisible();
  await expect(cards.first().getByText('视频：待生成', { exact: true })).toBeVisible();
  const firstCardButton = cards.first().getByRole('button', { name: '选择分镜 01', exact: true });
  await expect(firstCardButton).toHaveAccessibleDescription(/中景，林晚停在雨夜中的站台。/);
  await expect(firstCardButton).toHaveAccessibleDescription(new RegExp(`${state.shots[0].duration_ms / 1000}\\s*秒`));
  await expect(firstCardButton).toHaveAccessibleDescription(/图片：已采用[\s\S]*视频：待生成/);
  await expect(cards.nth(1).getByRole('button', { name: '选择分镜 02', exact: true })).toHaveAccessibleDescription(/图片：需核对/);
  const summary = cards.first().locator('.storyboard-summary-script');
  const clamp = await summary.evaluate(element => ({
    height: element.clientHeight, scroll: element.scrollHeight,
    lineHeight: parseFloat(getComputedStyle(element).lineHeight),
    lines: getComputedStyle(element).webkitLineClamp,
  }));
  expect(clamp.lines).toBe('2');
  expect(clamp.height).toBeCloseTo(clamp.lineHeight * 2, 0);
  expect(clamp.scroll).toBeGreaterThan(clamp.height);
  await expect(page.locator('.native-sound-mode')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('storyboard-cards.png'), fullPage: true });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('selection and adjacent navigation keep editing and media tools out of the grid', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  await expect(page.locator('.storyboard-summary').first()).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.storyboard-expanded')).toHaveCount(0);
  const panel = page.getByRole('complementary', { name: '分镜媒体设置', exact: true });
  const input = panel.getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await input.fill('固定机位，林晚收起旧信。');
  await panel.getByRole('button', { name: '下一个分镜', exact: true }).click();
  await expect(panel.getByRole('textbox', { name: '分镜 2 脚本', exact: true })).toBeVisible();
  await panel.getByRole('button', { name: '上一个分镜', exact: true }).click();
  await expect(input).toHaveValue('固定机位，林晚收起旧信。');
  await panel.getByRole('tab', { name: '分镜图', exact: true }).click();
  await expect(panel.getByRole('button', { name: '生成图片', exact: true })).toBeVisible();
  await panel.getByRole('spinbutton', { name: '图片数量', exact: true }).fill('3');
  await panel.getByRole('tab', { name: '镜头信息', exact: true }).click();
  await expect(input).toBeVisible();
  await panel.getByRole('tab', { name: '镜头信息', exact: true }).press('ArrowRight');
  await expect(panel.getByRole('tab', { name: '分镜图', exact: true })).toBeFocused();
  await expect(panel.getByRole('spinbutton', { name: '图片数量', exact: true })).toHaveValue('3');
  await expect.poll(() => state.shots[0].script).toBe('固定机位，林晚收起旧信。');
  await panel.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(panel.getByRole('region', { name: '分镜视频制作', exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('batch selection is explicit and stays selected when a card opens', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  await expect(page.getByRole('button', { name: '批量操作', exact: true })).toBeVisible();
  await expect(page.locator('.batch-item-select')).toHaveCount(0);
  await page.getByRole('button', { name: '批量操作', exact: true }).click();
  const card = page.locator('.storyboard-shot-card').first();
  await card.getByRole('checkbox', { name: '批量选择分镜 1', exact: true }).check();
  await expect(page.getByRole('checkbox', { name: '选择已加载项', exact: true })).toBeChecked({ indeterminate: true });
  await card.locator('.storyboard-summary').click();
  await expect(card.getByRole('checkbox')).toBeChecked();
  await page.getByRole('button', { name: '退出批量操作', exact: true }).click();
  await page.getByRole('button', { name: '批量操作', exact: true }).click();
  await expect(card.getByRole('checkbox')).toBeChecked();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('project assistant retains its draft while inline shot conflicts block navigation', async ({ page }) => {
  const state = await layoutFixture(page);
  page.on('console', message => { if (message.text().includes('Encountered two children with the same key')) state.errors.push(message.text()); });
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const conversation = { id: '301', project_id: '10', episode_id: null, title: '项目对话', stage: null, subject_type: null, subject_id: null, task_type: null, scope_version: 2, archived: false, row_version: '1', last_run_status: null, created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z' };
  const conversations = [conversation];
  await page.route(/\/api\/v1\/(agent|assistant)\//, async route => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/v1\/(agent|assistant)/, '');
    if (path === '/status') return route.fulfill({ json: { enabled: true, schema_ready: true } });
    if (path === '/models') return route.fulfill({ json: { items: [], preferred_id: null } });
    if (path === '/skills') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
    if (path === '/conversations/resolve') {
      expect(route.request().postDataJSON()).toEqual({ project_id: '10' });
      return route.fulfill({ json: conversation });
    }
    if (path === '/conversations') return route.fulfill({ json: { items: conversations, total: conversations.length, offset: 0, limit: 20 } });
    if (/^\/conversations\/\d+$/.test(path)) return route.fulfill({ json: conversations.find(item => item.id === path.split('/').pop()) });
    if (path.endsWith('/state')) return route.fulfill({ json: { conversation_id: path.split('/')[2], cursor: 0, active_run: null, queued_runs: [] } });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (/\/(messages|attachments|runs)$/.test(path)) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
    return route.fallback();
  });
  let conflict = true;
  await page.route(`**/api/v1${root}/shots/101`, async route => {
    if (route.request().method() === 'PATCH' && conflict) return route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } });
    return route.fallback();
  });
  await page.goto(`${root}/storyboard?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await expect(composer).toBeVisible();
  await composer.fill('保留这段项目对话草稿');
  await page.locator('.storyboard-summary').first().click();
  await expect(composer).toBeHidden();
  const script = page.getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await script.fill('发生冲突仍保留的镜头草稿');
  const stages = page.getByRole('navigation', { name: '分集制作流程' });
  await stages.getByRole('button', { name: /素材准备/ }).click();
  const confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(script).toHaveValue('发生冲突仍保留的镜头草稿');
  await expect(page.getByText(/你的输入仍保留/)).toBeVisible();
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  conflict = false;
  await stages.getByRole('button', { name: /素材准备/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '301');
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  expect(conversations).toHaveLength(1);
  expect(state.shots[0].script).toBe('发生冲突仍保留的镜头草稿');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

for (const width of [1440, 1024, 390, 320]) test(`cards and the selected-shot panel fit at ${width}px`, async ({ page }, info) => {
  const state = await layoutFixture(page);
  await page.setViewportSize({ width, height: 1000 });
  await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.locator('.storyboard-summary').first().click();
  await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`selected-shot-${width}.png`), fullPage: true });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});
