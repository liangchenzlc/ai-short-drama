import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import type { AgentSkillContent, AgentSkillDetail } from '../../src/api/types/agents';
import { fixture } from '../support/studio-fixture';

const personalId = '9007199254740995';
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII=';
const fileBytes = (file: { kind: string; content: string }) => Buffer.from(file.content, file.kind === 'image' ? 'base64' : 'utf8');
const time = '2026-10-22T00:00:00Z';
const metadata = { slug: 'continuity', description: '检查用户提供或主动选入的镜头，列出人物、道具与动作的连续性差异。', tags: ['连续性'], modes: ['standard', 'infinite_canvas'] as ('standard' | 'infinite_canvas')[], exclusions: '未提供的作品正文不在检查范围。', required_tools: [], source: '本项目测试夹具', compatible: true, issues: [], updated_at: time };
async function skillFixture(page: Page) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') void dialog.accept(); else { base.unexpected.push(`native dialog: ${dialog.message()}`); void dialog.dismiss(); } });
  const account = { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true };
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: account } }));
  const resources = new Map<string, { path: string; kind: 'reference' | 'template' | 'image'; content: string }[]>([
    [`${personalId}:3`, [{ path: 'references/continuity.md', kind: 'reference', content: '# 连续性检查\n' + '先核对同一人物与道具。🎬'.repeat(700) }, { path: 'assets/checklist.json', kind: 'template', content: '{"差异":[],"建议":[]}' }, { path: 'references/empty.txt', kind: 'reference', content: '' }, { path: 'assets/sample.png', kind: 'image', content: image }]],
  ]);
  function detail(id: string, body: AgentSkillContent, version = '1', rowVersion = '1'): AgentSkillDetail {
    const files = body.resources ?? [];
    return { ...metadata, ...body, id, builtin: false, filename: `${body.slug}.md`, enabled: body.enabled ?? true,
      settings_version: '1', content_version: version, row_version: rowVersion, checksum_sha256: hash(body.instructions), package_checksum: 'b'.repeat(64), resource_count: files.length,
      resources: files.map(file => ({ path: file.path, kind: file.kind, checksum_sha256: createHash('sha256').update(fileBytes(file)).digest('hex'), byte_size: fileBytes(file).length })) };
  }
  const personal = detail(personalId, { ...metadata, name: '雨夜连续性检查', instructions: '# 检查方法\n先列事实，再列差异。', resources: resources.get(`${personalId}:3`) }, '3', '9007199254740993');
  personal.settings_version = '9007199254740997';
  const builtin: AgentSkillDetail = { ...detail('script.v1', { ...metadata, name: '剧本创作', slug: 'script', instructions: '# 剧本方法\n先确认角色目标，再组织冲突。' }), builtin: true, filename: null, row_version: null, settings_version: '0' };
  const items = new Map([[personal.id, personal], [builtin.id, builtin]]);
  const calls: { path: string; method: string; body: Record<string, unknown> | null; key: string }[] = [];
  const reads: string[] = []; const receipts = new Map<string, { body: string; result: AgentSkillDetail }>();
  const state = { account, items, calls, reads, failList: false, failResource: false, abortCreate: false, abortCopy: false, abortSettings: false, abortUpdate: false, abortDelete: false, conflict: false, previewUnsupported: false, tagsProvided: false, resourceDelay: 0, modeIssues: {} as Record<string, string[]> };
  let nextId = 9007199254741010n;
  await page.route('**/api/v1/agent/skills**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1/agent'.length); const method = request.method();
    const json = request.headers()['content-type']?.includes('application/json'); const body = (json && ['POST', 'PATCH', 'DELETE'].includes(method) ? request.postDataJSON() : {}) as Partial<AgentSkillContent> & { row_version?: string; content_version?: string };
    const key = request.headers()['idempotency-key'] ?? ''; calls.push({ path, method, body, key });
    const reply = (data: unknown, status = 200) => route.fulfill({ json: data, status });
    if (path === '/skills' && method === 'GET') {
      if (state.failList) return reply({ error: { code: 'UNAVAILABLE', message: '目录暂时不可用。' } }, 503);
      const query = url.searchParams; const source = query.get('source');
      const selected = [...items.values()].filter(item => (!source || item.builtin === (source === 'builtin'))
        && (!query.get('query') || `${item.name} ${item.description}`.includes(query.get('query')!))
        && (!query.has('enabled') || item.enabled === (query.get('enabled') === 'true'))
        && (!query.get('mode') || item.modes.includes(query.get('mode') as 'standard' | 'infinite_canvas')));
      const offset = Number(query.get('offset') ?? 0);
      return reply({ items: selected.slice(offset, offset + 50).map(({ instructions: _text, resources: _files, ...summary }) => summary), total: selected.length, offset, limit: 50 });
    }
    if (path.startsWith('/skills/requests/')) {
      const receipt = receipts.get('/skills:' + decodeURIComponent(path.split('/')[3]));
      return receipt ? reply(receipt.result) : reply({ error: { code: 'NOT_FOUND' } }, 404);
    }
    if (path === '/skills/imports/preview') {
      if (state.previewUnsupported) return reply({ error: { code: 'invalid_agent_skill_package', message: '暂不支持脚本：scripts/run.py' } }, 422);
      return reply({ filename: 'rain.zip', draft: { ...metadata, name: '雨夜对话方法', modes: ['standard'], slug: 'rain-dialogue', description: '控制人物目标和对话节奏。', instructions: '# 对话方法\n控制单句长度。', resources: [{ path: 'assets/dialogue.md', kind: 'template', content: '角色：\n台词：' }], enabled: true }, tags_provided: state.tagsProvided, mode_issues: state.modeIssues, compatible: !state.previewUnsupported,
        issues: state.previewUnsupported ? ['scripts/run.py 是不支持的可执行脚本。'] : [], files: [{ path: 'SKILL.md', byte_size: 50, supported: true }, { path: 'assets/dialogue.md', byte_size: 20, supported: true }, ...(state.previewUnsupported ? [{ path: 'scripts/run.py', byte_size: 20, supported: false }] : [])], byte_size: 70 });
    }
    if (path === '/skills' && method === 'POST' || /\/copy$/.test(path) && method === 'POST') {
      const identity = `${path}:${key}`; const previous = receipts.get(identity);
      if (previous) return JSON.stringify(body) === previous.body ? reply(previous.result, 201) : reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      const copy = path.endsWith('/copy'); const original = copy ? items.get(path.split('/')[2])! : null;
      if (copy && original.content_version !== body.content_version) return reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      const content = copy ? { ...original!, name: original!.name + '（副本）', enabled: true, resources: resources.get(`${original!.id}:${original!.content_version}`) ?? [] } : body;
      const id = String(nextId++); const result = detail(id, content as AgentSkillContent);
      items.set(id, result); resources.set(`${id}:1`, content.resources ?? []); receipts.set(identity, { body: JSON.stringify(body), result });
      if (copy ? state.abortCopy : state.abortCreate) { if (copy) state.abortCopy = false; else state.abortCreate = false; return route.abort('failed'); }
      return reply(result, 201);
    }
    const resourcePath = path.match(/^\/skills\/([^/]+)\/versions\/(\d+)\/resources$/);
    if (resourcePath) {
      reads.push(path + url.search);
      if (state.resourceDelay && url.searchParams.get('path')?.endsWith('.md')) await new Promise(resolve => setTimeout(resolve, state.resourceDelay));
      if (state.failResource) return reply({ error: { code: 'UNAVAILABLE' } }, 503);
      const content = resources.get(`${resourcePath[1]}:${resourcePath[2]}`)?.find(file => file.path === url.searchParams.get('path'));
      if (!content) return reply({ error: { code: 'NOT_FOUND' } }, 404);
      const offset = Number(url.searchParams.get('offset')); const characters = Array.from(content.content); const text = characters.slice(offset, offset + 6000).join('');
      return reply({ skill_id: resourcePath[1], content_version: resourcePath[2], path: content.path, kind: content.kind, checksum_sha256: createHash('sha256').update(fileBytes(content)).digest('hex'), byte_size: fileBytes(content).length, offset, encoding: content.kind === 'image' ? 'base64' : 'utf-8', content: text, next_offset: offset + Array.from(text).length < characters.length ? offset + Array.from(text).length : null });
    }
    if (path.endsWith('/export')) return route.fulfill({ contentType: 'application/zip', body: Buffer.from('fixture archive') });
    const id = path.split('/')[2]; const item = items.get(id);
    if (!item) return reply({ error: { code: 'NOT_FOUND' } }, 404);
    if (path.endsWith('/settings')) {
      if (body.row_version !== item.settings_version) return reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      Object.assign(item, { enabled: body.enabled, settings_version: String(BigInt(item.settings_version) + 1n) });
      if (state.abortSettings) { state.abortSettings = false; return route.abort('failed'); }
      return reply(item);
    }
    if (method === 'GET') { reads.push(path); return reply(item); }
    if (method === 'PATCH') {
      if (state.conflict || body.row_version !== item.row_version) return reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      const version = String(BigInt(item.content_version) + 1n); const rowVersion = String(BigInt(item.row_version!) + 1n);
      const files = body.resources ?? resources.get(`${id}:${item.content_version}`) ?? [];
      const next = { ...detail(id, { ...item, ...body, resources: files }, version, rowVersion), settings_version: item.settings_version };
      items.set(id, next); resources.set(`${id}:${version}`, files);
      if (state.abortUpdate) { state.abortUpdate = false; return route.abort('failed'); }
      return reply(next);
    }
    if (method === 'DELETE') {
      if (body.row_version !== item.row_version) return reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      items.delete(id);
      if (state.abortDelete) { state.abortDelete = false; return route.abort('failed'); }
      return route.fulfill({ status: 204 });
    }
    base.unexpected.push(`${method} ${path}`); return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...base, state };
}

const writes = (state: Awaited<ReturnType<typeof skillFixture>>['state']) => state.calls.filter(call => call.method !== 'GET');
async function openPersonal(page: Page) { await page.getByRole('button', { name: '查看 雨夜连续性检查', exact: true }).click(); }
async function selectFile(page: Page, folder: string, name: string) {
  const node = page.locator('.ant-tree-treenode').filter({ has: page.getByText(folder, { exact: true }) }).first();
  if (await node.locator('.ant-tree-switcher_close').count()) await node.locator('.ant-tree-switcher').click();
  await page.getByText(name, { exact: true }).click();
}
async function importFile(page: Page) {
  await page.locator('input[type=file]').setInputFiles({ name: 'rain.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture package') });
  await expect(page.getByRole('heading', { name: '雨夜对话方法' })).toBeVisible();
}
test('legacy URL redirects with replace; AI config retains model categories and no Skill view', async ({ page }) => {
  const data = await skillFixture(page);
  await page.goto('/ai_config?section=skills&return_to=%2Fcanvas-app%2Fcanvas%2F20');
  await expect(page).toHaveURL(/\/skills\?return_to=/);
  await expect(page.getByRole('heading', { name: 'Skill 管理', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '返回画布' })).toBeVisible();
  await page.getByRole('link', { name: 'AI 配置', exact: true }).click();
  await expect(page.getByRole('tablist', { name: 'AI 配置类别' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Skill 管理', exact: true })).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: 'AI 配置视图' })).toHaveCount(0);
  expect(data.unexpected).toEqual([]);
});
test('cards show concise visible metadata; hidden tags do not match search', async ({ page }) => {
  const { state } = await skillFixture(page);
  await page.goto('/skills');
  const card = page.locator('.skill-card');
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('通用');
  await expect(card).not.toContainText(/文件|标签|自动|手动|版本|连续性，/);
  await page.getByRole('textbox', { name: '搜索 Skill' }).fill('隐藏标签');
  await expect(page.getByRole('heading', { name: '没有匹配的 Skill' })).toBeVisible();
  await expect(page).toHaveURL(/q=/);
  expect(state.calls.filter(call => call.method === 'GET').every(call => !call.path.includes('invocation_mode'))).toBe(true);
});
test('preview starts at SKILL.md; text stays readonly under typing, cut, paste and drop without writes', async ({ page }) => {
  const { state } = await skillFixture(page);
  await page.goto('/skills'); await openPersonal(page);
  const editor = page.getByRole('textbox', { name: '只读文件 SKILL.md', exact: true });
  await expect(editor).toContainText('# 检查方法');
  await expect(editor).toHaveAttribute('aria-readonly', 'true');
  await editor.focus(); await page.keyboard.type('cannot-write'); await page.keyboard.press('Control+A'); await page.keyboard.press('Control+X');
  await editor.evaluate(element => {
    const data = new DataTransfer(); data.setData('text/plain', 'cannot-paste');
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    element.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
  });
  await expect(editor).toHaveText('# 检查方法先列事实，再列差异。');
  await selectFile(page, 'assets', 'checklist.json');
  await expect(page.getByRole('textbox', { name: '只读文件 assets/checklist.json' })).toContainText('差异');
  expect(writes(state)).toEqual([]);
  await page.getByRole('button', { name: '关闭弹窗' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('unavailable Skills can be disabled but cannot be enabled again', async ({ page }) => {
  const { state } = await skillFixture(page);
  Object.assign(state.items.get(personalId)!, { compatible: false, issues: ['所需能力已退役，请更新文件'] });
  await page.goto('/skills');
  await expect(page.locator('.skill-card')).toContainText('所需能力已退役，请更新文件');
  const toggle = page.getByRole('switch', { name: '启用 雨夜连续性检查' });
  await expect(toggle).toBeEnabled(); await toggle.click();
  await expect(toggle).not.toBeChecked(); await expect(toggle).toBeDisabled();
  await openPersonal(page);
  await expect(page.getByRole('dialog').getByRole('switch')).toBeDisabled();
  expect(writes(state)).toHaveLength(1);
});
test('resource failure retries locally, and a cancelled slow file never replaces current text', async ({ page }) => {
  const { state } = await skillFixture(page);
  await page.goto('/skills'); await openPersonal(page);
  state.failResource = true;
  await selectFile(page, 'assets', 'checklist.json');
  await expect(page.getByRole('alert')).toContainText('读取失败');
  await expect(page.getByRole('tree', { name: 'Skill 文件目录' })).toBeVisible();
  state.failResource = false; await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '只读文件 assets/checklist.json' })).toContainText('差异');
  state.resourceDelay = 400;
  await selectFile(page, 'references', 'continuity.md');
  await page.getByText('checklist.json', { exact: true }).click();
  await expect(page.getByRole('textbox', { name: '只读文件 assets/checklist.json' })).toContainText('差异');
  await page.waitForTimeout(500);
  await expect(page.getByRole('textbox', { name: '只读文件 references/continuity.md' })).toHaveCount(0);
  expect(writes(state)).toEqual([]);
});
test('new import defaults to general regardless of package modes and offers no metadata fields', async ({ page }) => {
  const { state } = await skillFixture(page);
  await page.goto('/skills'); await page.getByRole('button', { name: '导入 Skill', exact: true }).first().click(); await importFile(page);
  await expect(page.getByRole('radio', { name: '通用', exact: true })).toBeChecked();
  await expect(page.getByRole('textbox', { name: /名称|用途|标签|主指令/ })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '只读文件 SKILL.md', exact: true })).toContainText('控制单句长度');
  await page.getByText('画布', { exact: true }).click();
  await page.getByRole('button', { name: '确认导入', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看 雨夜对话方法', exact: true })).toBeVisible();
  const created = writes(state).find(call => call.path === '/skills');
  expect(created?.body).toMatchObject({ enabled: true, modes: ['infinite_canvas'] });
  expect(created?.body).not.toHaveProperty('invocation_mode');
});
test('updates replace the complete package and preserve enabled, modes and hidden tags', async ({ page }) => {
  const { state } = await skillFixture(page);
  Object.assign(state.items.get(personalId)!, { enabled: false, modes: ['standard'], tags: ['隐藏标签'] });
  await page.goto('/skills'); await openPersonal(page);
  await page.getByRole('button', { name: '更新文件', exact: true }).click(); await importFile(page);
  await expect(page.getByRole('radio', { name: '创作', exact: true })).toBeChecked();
  await page.getByText('查看变更', { exact: true }).click();
  await expect(page.getByText(/移除：/)).toContainText('references/continuity.md');
  await page.getByRole('button', { name: '确认更新', exact: true }).click();
  await page.getByRole('dialog', { name: '确认完整包替换' }).getByRole('button', { name: '确认更新', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看 雨夜对话方法', exact: true })).toBeVisible();
  const updated = writes(state).find(call => call.method === 'PATCH');
  expect(updated?.body).toMatchObject({ row_version: '9007199254740993', modes: ['standard'] });
  expect(updated?.body).not.toHaveProperty('tags'); expect(updated?.body).not.toHaveProperty('enabled');
  expect(state.items.get(personalId)?.enabled).toBe(false);
  expect(state.items.get(personalId)?.tags).toEqual(['隐藏标签']);
});
test('script packages reject the whole import and cannot be confirmed', async ({ page }) => {
  const { state } = await skillFixture(page); state.previewUnsupported = true;
  await page.goto('/skills'); await page.getByRole('button', { name: '导入 Skill', exact: true }).first().click();
  await page.locator('input[type=file]').setInputFiles({ name: 'scripts.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture') });
  await expect(page.getByText('暂不支持脚本：scripts/run.py', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认导入' })).toHaveCount(0);
  expect(writes(state).filter(call => call.path === '/skills')).toEqual([]);
});
test('unknown accepted create survives refresh and reconciles by original receipt without duplicating', async ({ page }) => {
  const { state } = await skillFixture(page); state.abortCreate = true;
  await page.goto('/skills'); await page.getByRole('button', { name: '导入 Skill', exact: true }).first().click(); await importFile(page);
  await page.getByRole('button', { name: '确认导入', exact: true }).click();
  await expect(page.getByRole('button', { name: '核对导入结果' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '恢复导入记录' }).click();
  await page.getByRole('button', { name: '核对导入结果' }).click();
  await expect(page.getByRole('button', { name: '查看 雨夜对话方法', exact: true })).toBeVisible();
  expect(writes(state).filter(call => call.path === '/skills')).toHaveLength(1);
});
test('settings recovery is independent of content and sends no trigger field', async ({ page }) => {
  const { state } = await skillFixture(page); state.abortSettings = true;
  await page.goto('/skills'); await page.getByRole('switch', { name: '启用 雨夜连续性检查' }).click();
  await expect(page.getByRole('dialog', { name: '核对 Skill 操作' })).toBeVisible();
  await page.getByRole('button', { name: '核对原操作' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(writes(state)).toHaveLength(1);
  expect(writes(state)[0].body).toEqual({ row_version: '9007199254740997', enabled: false });
});
test('desktop preview keeps the directory and file visible together without overflow', async ({ page }) => {
  await skillFixture(page); await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/skills'); await openPersonal(page);
  const tree = page.getByRole('tree', { name: 'Skill 文件目录' });
  await expect(tree).toBeVisible();
  await expect(page.getByRole('textbox', { name: '只读文件 SKILL.md', exact: true })).toBeVisible();
  await selectFile(page, 'assets', 'checklist.json');
  await expect(tree).toBeVisible();
  await expect(page.getByRole('textbox', { name: '只读文件 assets/checklist.json' })).toBeVisible();
  await expect(page.getByRole('button', { name: '文件', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('preview remains navigable with long names in a short desktop window', async ({ page }) => {
  const { state } = await skillFixture(page);
  const name = '雨夜连续性与人物道具动作校准'.repeat(8);
  state.items.get(personalId)!.name = name;
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.goto('/skills'); await page.getByRole('button', { name: `查看 ${name}`, exact: true }).click();
  const dialog = page.getByRole('dialog', { name, exact: true });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('textbox', { name: '只读文件 SKILL.md', exact: true })).toBeVisible();
  await expect(page.getByRole('tree', { name: 'Skill 文件目录' })).toBeVisible();
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.getByRole('button', { name: '关闭弹窗' }).focus();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByRole('textbox', { name: '只读文件 SKILL.md', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('long descriptions expand according to visible overflow and collapse again', async ({ page }) => {
  const { state } = await skillFixture(page);
  const description = '检查人物、道具与动作的连续性差异，并将建议按镜头顺序列出。'.repeat(12);
  state.items.get(personalId)!.description = description;
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.goto('/skills'); await openPersonal(page);
  await page.getByRole('button', { name: '展开', exact: true }).click();
  await expect(page.locator('.skill-preview-purpose')).toContainText(description);
  await page.getByRole('button', { name: '收起', exact: true }).click();
  await expect(page.getByRole('button', { name: '展开', exact: true })).toBeVisible();
});

test('Unicode chunks, empty files and images use the same readonly browser', async ({ page }) => {
  const { state } = await skillFixture(page);
  await page.goto('/skills'); await openPersonal(page);
  await selectFile(page, 'references', 'continuity.md');
  const editor = page.getByRole('textbox', { name: '只读文件 references/continuity.md' });
  await expect(editor).toContainText('🎬');
  expect(state.reads.filter(path => path.includes('continuity.md'))).toHaveLength(2);
  await page.getByText('empty.txt', { exact: true }).click();
  await expect(page.getByRole('textbox', { name: '只读文件 references/empty.txt' })).toHaveText('');
  await selectFile(page, 'assets', 'sample.png');
  await expect(page.getByRole('img', { name: 'assets/sample.png' })).toBeVisible();
  expect(writes(state)).toEqual([]);
});

test('mode compatibility blocks import until an available mode is selected', async ({ page }) => {
  const { state } = await skillFixture(page);
  state.modeIssues = { general: ['所选模式不支持这个方法需要的能力'], infinite_canvas: ['所选模式不支持这个方法需要的能力'] };
  await page.goto('/skills'); await page.getByRole('button', { name: '导入 Skill', exact: true }).first().click(); await importFile(page);
  await expect(page.getByRole('button', { name: '确认导入' })).toBeDisabled();
  await page.getByText('创作', { exact: true }).click();
  await expect(page.getByRole('button', { name: '确认导入' })).toBeEnabled();
  await page.getByRole('button', { name: '确认导入' }).click();
  expect(writes(state).find(call => call.path === '/skills')?.body?.modes).toEqual(['standard']);
});

test('an accepted replacement is reconciled after refresh without another write', async ({ page }) => {
  const { state } = await skillFixture(page); state.abortUpdate = true;
  await page.goto('/skills'); await openPersonal(page);
  await page.getByRole('button', { name: '更新文件' }).click(); await importFile(page);
  await page.getByRole('button', { name: '确认更新', exact: true }).click();
  await page.getByRole('dialog', { name: '确认完整包替换' }).getByRole('button', { name: '确认更新' }).click();
  await expect(page.getByRole('button', { name: '核对导入结果' })).toBeVisible();
  await page.reload(); await page.getByRole('button', { name: '恢复导入记录' }).click();
  await page.getByRole('button', { name: '核对导入结果' }).click();
  await expect(page.getByRole('button', { name: '查看 雨夜对话方法', exact: true })).toBeVisible();
  expect(writes(state).filter(call => call.method === 'PATCH')).toHaveLength(1);
});

test('conflicting replacements keep the package and require explicit use of the current version', async ({ page }) => {
  const { state } = await skillFixture(page);
  await page.goto('/skills'); await openPersonal(page);
  await page.getByRole('button', { name: '更新文件' }).click(); await importFile(page);
  const latest = state.items.get(personalId)!;
  latest.row_version = '9007199254740994'; latest.instructions = '其他窗口保存的正文';
  await page.getByRole('button', { name: '确认更新', exact: true }).click();
  await page.getByRole('dialog', { name: '确认完整包替换' }).getByRole('button', { name: '确认更新' }).click();
  await expect(page.getByRole('button', { name: '读取当前版本' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '只读文件 SKILL.md' })).toContainText('控制单句长度');
  expect(writes(state).filter(call => call.method === 'PATCH')).toHaveLength(1);
  await page.getByRole('button', { name: '读取当前版本' }).click();
  await page.getByRole('button', { name: '查看当前文件' }).click();
  await expect(page.getByRole('textbox', { name: '只读文件 SKILL.md' })).toContainText('其他窗口保存的正文');
  await page.getByRole('button', { name: '按当前版本更新', exact: true }).click();
  await page.locator('.studio-confirm-dialog').getByRole('button', { name: '按当前版本更新', exact: true }).click();
  await page.locator('.skill-dialog-footer').getByRole('button', { name: /确认更新$/ }).click();
  await page.getByRole('dialog', { name: '确认完整包替换' }).getByRole('button', { name: '确认更新' }).click();
  await expect(page.getByRole('button', { name: '查看 雨夜对话方法', exact: true })).toBeVisible();
  expect(writes(state).filter(call => call.method === 'PATCH').map(call => call.body?.row_version)).toEqual(['9007199254740993', '9007199254740994']);
});

test('retired pending requests remain downloadable and are never rewritten or resubmitted', async ({ page }) => {
  const { state } = await skillFixture(page);
  const values = { ...metadata, name: '旧草稿', instructions: '旧方法正文', enabled: true, invocation_mode: 'manual', resources: [] };
  const draft = { schema_version: 1, key: 'legacy-create', base: null, values, changed: true, pending: { key: 'legacy-create', body: values, row_version: null }, conflict: false };
  await page.addInitScript(({ account, draft }) => localStorage.setItem(`agent-skill-draft:v1:${account}`, JSON.stringify(draft)), { account: state.account.id, draft });
  await page.goto('/skills'); await page.getByRole('button', { name: '恢复旧 Skill记录' }).click();
  await page.getByRole('button', { name: '核对导入结果' }).click();
  await expect.poll(() => state.calls.some(call => call.path === '/skills/requests/legacy-create')).toBe(true);
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '下载记录', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('skill-recovery.json');
  expect(writes(state)).toEqual([]);
});

test('retired settings requests offer download and explicit clearing without resending', async ({ page }) => {
  const { state } = await skillFixture(page);
  const action = { kind: 'settings', item: state.items.get(personalId)!, enabled: false, invocation_mode: 'manual' };
  await page.addInitScript(({ account, action }) => localStorage.setItem(`agent-skill-action:v1:${account}`, JSON.stringify(action)), { account: state.account.id, action });
  await page.goto('/skills');
  await expect(page.getByRole('dialog', { name: '核对 Skill 操作' })).toBeVisible();
  await expect(page.getByRole('button', { name: '核对原操作' })).toHaveCount(0);
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '下载记录', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('skill-operation.json');
  expect(writes(state)).toEqual([]);
});
