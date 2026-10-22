import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import type { AgentSkillContent, AgentSkillDetail } from '../../src/api/types/agents';
import { fixture } from '../support/studio-fixture';

const personalId = '9007199254740995';
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const time = '2026-10-22T00:00:00Z';
const metadata = { slug: 'continuity', description: '检查用户提供或主动选入的镜头，列出人物、道具与动作的连续性差异。', tags: ['连续性'], modes: ['standard', 'infinite_canvas'] as ('standard' | 'infinite_canvas')[], exclusions: '未提供的作品正文不在检查范围。', required_tools: [], source: '本项目测试夹具', invocation_mode: 'auto' as const, compatible: true, issues: [], updated_at: time };
async function skillFixture(page: Page) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') void dialog.accept(); else { base.unexpected.push(`native dialog: ${dialog.message()}`); void dialog.dismiss(); } });
  const account = { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true };
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: account } }));
  const resources = new Map<string, { path: string; kind: 'reference' | 'template'; content: string }[]>([
    [`${personalId}:3`, [{ path: 'references/continuity.md', kind: 'reference', content: '# 连续性检查\n' + '先核对同一人物与道具。'.repeat(700) }, { path: 'assets/checklist.json', kind: 'template', content: '{"差异":[],"建议":[]}' }]],
  ]);
  function detail(id: string, body: AgentSkillContent, version = '1', rowVersion = '1'): AgentSkillDetail {
    const files = body.resources ?? [];
    return { ...metadata, ...body, id, builtin: false, filename: `${body.slug}.md`, enabled: body.enabled ?? true, invocation_mode: body.invocation_mode ?? 'auto',
      settings_version: '1', content_version: version, row_version: rowVersion, checksum_sha256: hash(body.instructions), package_checksum: 'b'.repeat(64), resource_count: files.length,
      resources: files.map(file => ({ path: file.path, kind: file.kind, checksum_sha256: hash(file.content), byte_size: Buffer.byteLength(file.content) })) };
  }
  const personal = detail(personalId, { ...metadata, name: '雨夜连续性检查', instructions: '# 检查方法\n先列事实，再列差异。', resources: resources.get(`${personalId}:3`) }, '3', '9007199254740993');
  personal.settings_version = '9007199254740997';
  const builtin: AgentSkillDetail = { ...detail('script.v1', { ...metadata, name: '剧本创作', slug: 'script', instructions: '# 剧本方法\n先确认角色目标，再组织冲突。' }), builtin: true, filename: null, row_version: null, settings_version: '0' };
  const items = new Map([[personal.id, personal], [builtin.id, builtin]]);
  const calls: { path: string; method: string; body: any; key: string }[] = [];
  const reads: string[] = []; const receipts = new Map<string, { body: string; result: AgentSkillDetail }>();
  const state = { account, items, calls, reads, failList: false, failResource: false, abortCreate: false, abortCopy: false, abortSettings: false, abortUpdate: false, abortDelete: false, conflict: false, previewUnsupported: false };
  let nextId = 9007199254741010n;
  await page.route('**/api/v1/agent/skills**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1/agent'.length); const method = request.method();
    const json = request.headers()['content-type']?.includes('application/json'); const body = json && ['POST', 'PATCH', 'DELETE'].includes(method) ? request.postDataJSON() : null;
    const key = request.headers()['idempotency-key'] ?? ''; calls.push({ path, method, body, key });
    const reply = (data: unknown, status = 200) => route.fulfill({ json: data, status });
    if (path === '/skills' && method === 'GET') {
      if (state.failList) return reply({ error: { code: 'UNAVAILABLE', message: '目录暂时不可用。' } }, 503);
      const query = url.searchParams; const source = query.get('source');
      const selected = [...items.values()].filter(item => (!source || item.builtin === (source === 'builtin'))
        && (!query.get('query') || `${item.name} ${item.description} ${item.tags.join(' ')}`.includes(query.get('query')!))
        && (!query.has('enabled') || item.enabled === (query.get('enabled') === 'true'))
        && (!query.get('invocation_mode') || item.invocation_mode === query.get('invocation_mode'))
        && (!query.get('mode') || item.modes.includes(query.get('mode') as 'standard' | 'infinite_canvas')));
      const offset = Number(query.get('offset') ?? 0);
      return reply({ items: selected.slice(offset, offset + 50).map(({ instructions: _text, resources: _files, ...summary }) => summary), total: selected.length, offset, limit: 50 });
    }
    if (path === '/skills/imports/preview') {
      return reply({ filename: 'rain.zip', draft: { ...metadata, name: '雨夜对话方法', slug: 'rain-dialogue', description: '', instructions: '# 对话方法\n控制单句长度。', resources: [{ path: 'assets/dialogue.md', kind: 'template', content: '角色：\n台词：' }], enabled: true, invocation_mode: 'auto' }, compatible: !state.previewUnsupported,
        issues: state.previewUnsupported ? ['scripts/run.py 是不支持的可执行脚本。'] : [], files: [{ path: 'SKILL.md', byte_size: 50, supported: true }, { path: 'assets/dialogue.md', byte_size: 20, supported: true }, ...(state.previewUnsupported ? [{ path: 'scripts/run.py', byte_size: 20, supported: false }] : [])], byte_size: 70 });
    }
    if (path === '/skills' && method === 'POST' || /\/copy$/.test(path) && method === 'POST') {
      const identity = `${path}:${key}`; const previous = receipts.get(identity);
      if (previous) return JSON.stringify(body) === previous.body ? reply(previous.result, 201) : reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      const copy = path.endsWith('/copy'); const original = copy ? items.get(path.split('/')[2])! : null;
      if (copy && original.content_version !== body.content_version) return reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      const content = copy ? { ...original!, name: original!.name + '（副本）', enabled: true, invocation_mode: 'auto', resources: resources.get(`${original!.id}:${original!.content_version}`) ?? [] } : body;
      const id = String(nextId++); const result = detail(id, content);
      items.set(id, result); resources.set(`${id}:1`, content.resources ?? []); receipts.set(identity, { body: JSON.stringify(body), result });
      if (copy ? state.abortCopy : state.abortCreate) { if (copy) state.abortCopy = false; else state.abortCreate = false; return route.abort('failed'); }
      return reply(result, 201);
    }
    const resourcePath = path.match(/^\/skills\/([^/]+)\/versions\/(\d+)\/resources$/);
    if (resourcePath) {
      reads.push(path + url.search);
      if (state.failResource) return reply({ error: { code: 'UNAVAILABLE' } }, 503);
      const content = resources.get(`${resourcePath[1]}:${resourcePath[2]}`)?.find(file => file.path === url.searchParams.get('path'));
      if (!content) return reply({ error: { code: 'NOT_FOUND' } }, 404);
      const offset = Number(url.searchParams.get('offset')); const text = content.content.slice(offset, offset + 6000);
      return reply({ skill_id: resourcePath[1], content_version: resourcePath[2], path: content.path, kind: content.kind, checksum_sha256: hash(content.content), byte_size: Buffer.byteLength(content.content), offset, content: text, next_offset: offset + text.length < content.content.length ? offset + text.length : null });
    }
    if (path.endsWith('/export')) return route.fulfill({ contentType: 'application/zip', body: Buffer.from('fixture archive') });
    const id = path.split('/')[2]; const item = items.get(id);
    if (!item) return reply({ error: { code: 'NOT_FOUND' } }, 404);
    if (path.endsWith('/settings')) {
      if (body.row_version !== item.settings_version) return reply({ error: { code: 'VERSION_CONFLICT' } }, 409);
      Object.assign(item, { enabled: body.enabled, invocation_mode: body.invocation_mode, settings_version: String(BigInt(item.settings_version) + 1n) });
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
const row = (page: Page, name = '雨夜连续性检查') => page.getByRole('row').filter({ has: page.getByText(name, { exact: true }) });
async function createFields(page: Page, name = '雨夜人物方法') {
  await page.getByRole('textbox', { name: '显示名称', exact: true }).fill(name);
  await page.getByRole('textbox', { name: '用途描述', exact: true }).fill('整理用户提供的人物动机。');
  await page.getByRole('textbox', { name: 'Markdown 主指令', exact: true }).fill('# 人物方法\n先确认人物目标，再整理对白。');
}

test('Skill library: Skill management uses a separate deep link, summary filters and independent personal settings', async ({ page }) => {
  const data = await skillFixture(page);
  await page.goto('/ai_config?section=skills&return_to=%2Fcanvas-app%2Fcanvas%2Frain');
  await expect(row(page)).toBeVisible(); expect(data.state.reads).toEqual([]);
  await expect(page.getByRole('button', { name: '返回画布', exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '调用方式 雨夜连续性检查', exact: true }).selectOption('manual');
  await expect.poll(() => data.state.items.get(personalId)?.invocation_mode).toBe('manual');
  expect(data.state.calls.find(call => call.path.endsWith('/settings'))?.body).toEqual({ row_version: '9007199254740997', enabled: true, invocation_mode: 'manual' });
  expect(data.state.items.get(personalId)?.content_version).toBe('3');
  await page.getByRole('textbox', { name: '搜索 Skill', exact: true }).fill('不存在的方法'); await page.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page.getByRole('heading', { name: '没有匹配的 Skill', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '清除筛选', exact: true }).first().click(); await expect(row(page)).toBeVisible();
  await page.getByRole('tab', { name: '模型与助手', exact: true }).click();
  await expect(page).toHaveURL(/return_to=/); await expect(page).not.toHaveURL(/section=skills/);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: online creation keeps the original request across an accepted response loss and reload', async ({ page }) => {
  const data = await skillFixture(page); data.state.abortCreate = true;
  await page.goto('/ai_config?section=skills'); await page.getByRole('button', { name: '新建 Skill', exact: true }).click();
  await createFields(page); await page.getByRole('button', { name: '添加模板', exact: true }).click();
  await page.getByRole('textbox', { name: /^资料正文 / }).fill('人物：\n目标：');
  await page.getByRole('button', { name: '保存 Skill', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用原请求核对保存', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toBeDisabled();
  await page.reload(); await page.getByRole('button', { name: '使用原请求核对保存', exact: true }).click();
  await expect(row(page, '雨夜人物方法')).toBeVisible();
  const calls = data.state.calls.filter(call => call.path === '/skills' && call.method === 'POST');
  expect(calls).toHaveLength(2); expect(calls[1].key).toBe(calls[0].key); expect(calls[1].body).toEqual(calls[0].body);
  expect(calls[0].body).toMatchObject({ enabled: true, invocation_mode: 'auto', resources: [{ kind: 'template', content: '人物：\n目标：' }] });
  expect([...data.state.items.values()].filter(item => item.name === '雨夜人物方法')).toHaveLength(1);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: editing reads immutable resources in bounded pages and reconciles an accepted patch without replaying it', async ({ page }) => {
  const data = await skillFixture(page); data.state.abortUpdate = true;
  await page.goto('/ai_config?section=skills'); await row(page).getByRole('button', { name: '编辑', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '资料正文 references/continuity.md', exact: true })).toContainText('先核对同一人物');
  expect(data.state.reads.some(path => path.includes('offset=6000'))).toBe(true);
  await page.getByRole('textbox', { name: 'Markdown 主指令', exact: true }).fill('# 修订方法\n保留人物动机，列出证据。');
  await page.getByRole('button', { name: '保存 Skill', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用原请求核对保存', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '使用原请求核对保存', exact: true }).click(); await expect(row(page)).toBeVisible();
  const calls = data.state.calls.filter(call => call.path === `/skills/${personalId}` && call.method === 'PATCH');
  expect(calls).toHaveLength(1); expect(calls[0].body.row_version).toBe('9007199254740993'); expect(calls[0].body.resources).toHaveLength(2);
  expect(calls[0].body).not.toHaveProperty('enabled'); expect(calls[0].body).not.toHaveProperty('invocation_mode');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: a conflict preserves the draft, compares the server version and requires an explicit choice', async ({ page }) => {
  const data = await skillFixture(page);
  await page.goto('/ai_config?section=skills'); await row(page).getByRole('button', { name: '编辑', exact: true }).click();
  await page.getByRole('textbox', { name: 'Markdown 主指令', exact: true }).fill('# 本地修改\n保留雨夜对白。');
  const server = data.state.items.get(personalId)!; server.instructions = '# 服务端修改\n保留镜头节奏。'; server.row_version = '9007199254740994'; server.content_version = '4';
  await page.getByRole('button', { name: '保存 Skill', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Markdown 主指令', exact: true })).toHaveValue('# 本地修改\n保留雨夜对白。');
  await page.getByRole('button', { name: '读取服务端版本', exact: true }).click();
  await expect(page.getByRole('region', { name: '服务端版本比较', exact: true })).toContainText('保留镜头节奏。');
  expect(data.state.calls.filter(call => call.method === 'PATCH')).toHaveLength(1);
  await page.getByRole('button', { name: '保留草稿，基于此版本继续', exact: true }).click();
  await page.getByRole('dialog', { name: '核对 Skill 版本', exact: true }).getByRole('button', { name: '保留草稿继续', exact: true }).click();
  await page.getByRole('button', { name: '保存 Skill', exact: true }).click(); await expect(row(page)).toBeVisible();
  expect(data.state.calls.filter(call => call.method === 'PATCH').at(-1)?.body.row_version).toBe('9007199254740994');
  expect(data.state.items.get(personalId)?.instructions).toBe('# 本地修改\n保留雨夜对白。');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: Markdown and ZIP previews enter the same editor while unsupported scripts remain blocked', async ({ page }) => {
  const data = await skillFixture(page); data.state.previewUnsupported = true;
  await page.goto('/ai_config?section=skills'); await page.getByRole('button', { name: '导入 Skill', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导入 Skill', exact: true });
  await dialog.getByLabel('导入 Skill 文件', { exact: true }).setInputFiles({ name: 'rain.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture archive') });
  await expect(dialog.getByText('scripts/run.py 是不支持的可执行脚本。', { exact: true })).toBeVisible(); await expect(dialog.getByRole('button', { name: '进入编辑', exact: true })).toBeDisabled();
  data.state.previewUnsupported = false;
  await dialog.getByLabel('导入 Skill 文件', { exact: true }).setInputFiles({ name: 'rain.md', mimeType: 'text/markdown', buffer: Buffer.from('# 对话方法') });
  await expect(dialog.getByRole('button', { name: '进入编辑', exact: true })).toBeEnabled(); await dialog.getByRole('button', { name: '进入编辑', exact: true }).click();
  await expect(dialog).toHaveCount(0); await page.getByRole('textbox', { name: '用途描述', exact: true }).fill('整理主动选入的人物对白。');
  await expect(page.getByRole('checkbox', { name: '启用此 Skill', exact: true })).toBeChecked();
  await page.getByRole('button', { name: '保存 Skill', exact: true }).click(); await expect(row(page, '雨夜对话方法')).toBeVisible();
  expect(data.state.calls.filter(call => call.path === '/skills' && call.method === 'POST')).toHaveLength(1);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: builtins remain read-only and copying recovers with the original key before exporting and deleting the independent copy', async ({ page }) => {
  const data = await skillFixture(page); data.state.abortCopy = true;
  await page.goto('/ai_config?section=skills'); await page.getByText('内置 Skill', { exact: true }).click();
  await row(page, '剧本创作').getByRole('button', { name: '查看', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Markdown 主指令', exact: true })).toHaveAttribute('readonly', '');
  await expect(page.getByRole('button', { name: '保存 Skill', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '复制为我的 Skill', exact: true }).click();
  const pending = page.getByRole('dialog', { name: '核对 Skill 操作', exact: true }); await expect(pending).toBeVisible();
  await pending.getByRole('button', { name: '使用原请求核对复制', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveValue('剧本创作（副本）');
  const copies = data.state.calls.filter(call => call.path.endsWith('/copy')); expect(copies).toHaveLength(2); expect(copies[0].key).toBe(copies[1].key);
  await page.getByRole('button', { name: '返回列表', exact: true }).click(); await page.getByText('我的 Skill', { exact: true }).click();
  await row(page, '剧本创作（副本）').getByRole('button', { name: '更多操作：剧本创作（副本）', exact: true }).click();
  const download = page.waitForEvent('download'); await page.getByRole('menuitem', { name: '导出 ZIP', exact: true }).click(); expect((await download).suggestedFilename()).toBe('script.zip');
  await row(page, '剧本创作（副本）').getByRole('button', { name: '更多操作：剧本创作（副本）', exact: true }).click(); await page.getByRole('menuitem', { name: '删除 Skill', exact: true }).click();
  await page.getByRole('dialog', { name: '删除 Skill', exact: true }).getByRole('button', { name: '删除 Skill', exact: true }).click();
  await expect(row(page, '剧本创作（副本）')).toHaveCount(0); expect(data.state.items.get('script.v1')?.instructions).toContain('剧本方法');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: an accepted setting loss resolves by reading and a retained draft survives guarded navigation', async ({ page }) => {
  const data = await skillFixture(page); data.state.abortSettings = true;
  await page.goto('/ai_config?section=skills'); await row(page).getByRole('switch', { name: '启用 雨夜连续性检查', exact: true }).click();
  const pending = page.getByRole('dialog', { name: '核对 Skill 操作', exact: true }); await expect(pending).toBeVisible(); await page.reload();
  await page.getByRole('dialog', { name: '核对 Skill 操作', exact: true }).getByRole('button', { name: '读取当前状态并核对', exact: true }).click();
  await expect(row(page).getByRole('switch')).not.toBeChecked(); expect(data.state.calls.filter(call => call.path.endsWith('/settings'))).toHaveLength(1);
  await row(page).getByRole('button', { name: '编辑', exact: true }).click(); await page.getByRole('textbox', { name: '显示名称', exact: true }).fill('保留的雨夜草稿');
  await page.getByRole('tab', { name: '模型与助手', exact: true }).click(); const guard = page.getByRole('dialog', { name: '保留 Skill 草稿', exact: true });
  await guard.getByRole('button', { name: '取消', exact: true }).click(); await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveValue('保留的雨夜草稿');
  await page.getByRole('tab', { name: '模型与助手', exact: true }).click(); await page.getByRole('dialog', { name: '保留 Skill 草稿', exact: true }).getByRole('button', { name: '保留并离开', exact: true }).click();
  await page.getByRole('tab', { name: 'Skill 管理', exact: true }).click(); await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveValue('保留的雨夜草稿');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: setting conflicts require a comparison and explicit use of the latest version', async ({ page }) => {
  const data = await skillFixture(page);
  await page.goto('/ai_config?section=skills'); await expect(row(page)).toBeVisible();
  Object.assign(data.state.items.get(personalId)!, { settings_version: '9007199254740998', invocation_mode: 'manual' });
  await row(page).getByRole('switch', { name: '启用 雨夜连续性检查', exact: true }).click();
  const pending = page.getByRole('dialog', { name: '核对 Skill 操作', exact: true });
  await pending.getByRole('button', { name: '读取当前状态并核对', exact: true }).click();
  await expect(pending).toContainText('当前服务端：雨夜连续性检查');
  expect(data.state.calls.filter(call => call.path.endsWith('/settings'))).toHaveLength(1);
  await pending.getByRole('button', { name: '按当前版本继续', exact: true }).click();
  const confirm = page.getByRole('dialog', { name: '核对最新 Skill 状态', exact: true });
  await expect(confirm).toBeVisible();
  expect(data.state.calls.filter(call => call.path.endsWith('/settings'))).toHaveLength(1);
  await confirm.getByRole('button', { name: '按此版本继续', exact: true }).click();
  await expect(pending).toHaveCount(0); await expect(row(page).getByRole('switch')).not.toBeChecked();
  const calls = data.state.calls.filter(call => call.path.endsWith('/settings'));
  expect(calls.map(call => call.body.row_version)).toEqual(['9007199254740997', '9007199254740998']);
  expect(data.state.items.get(personalId)?.content_version).toBe('3');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: an accepted deletion loss is reconciled after reload without another delete', async ({ page }) => {
  const data = await skillFixture(page); data.state.abortDelete = true;
  await page.goto('/ai_config?section=skills');
  await row(page).getByRole('button', { name: '更多操作：雨夜连续性检查', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除 Skill', exact: true }).click();
  await page.getByRole('dialog', { name: '删除 Skill', exact: true }).getByRole('button', { name: '删除 Skill', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '核对 Skill 操作', exact: true })).toBeVisible(); await page.reload();
  await page.getByRole('dialog', { name: '核对 Skill 操作', exact: true }).getByRole('button', { name: '读取当前状态并核对', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '核对 Skill 操作', exact: true })).toHaveCount(0);
  await expect(row(page)).toHaveCount(0); expect(data.state.calls.filter(call => call.method === 'DELETE')).toHaveLength(1);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: unavailable local storage blocks initial mutations and recovery retries', async ({ page }) => {
  const data = await skillFixture(page);
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith('agent-skill-')) throw new Error('storage quota');
      return original.call(this, key, value);
    };
  });
  await page.goto('/ai_config?section=skills'); await row(page).getByRole('switch').click();
  await expect(page.getByRole('alert').filter({ hasText: '原操作记录未能可靠保存' })).toBeVisible();
  expect(data.state.calls.filter(call => call.path.endsWith('/settings'))).toHaveLength(0);
  await page.getByRole('button', { name: '新建 Skill', exact: true }).click(); await createFields(page, '仍在本机的草稿');
  await page.getByRole('button', { name: '保存 Skill', exact: true }).click();
  const retry = page.getByRole('button', { name: '使用原请求核对保存', exact: true });
  await expect(retry).toBeVisible();
  expect(data.state.calls.filter(call => call.path === '/skills' && call.method === 'POST')).toHaveLength(0);
  await retry.click();
  await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveValue('仍在本机的草稿');
  expect(data.state.calls.filter(call => call.path === '/skills' && call.method === 'POST')).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: switching accounts clears editor state and restores only that account draft', async ({ page }) => {
  const data = await skillFixture(page); const firstAccount = data.state.account.id;
  await page.goto('/ai_config?section=skills'); await page.getByRole('button', { name: '新建 Skill', exact: true }).click();
  await createFields(page, '甲账号的雨夜草稿');
  data.state.account.id = '9007199254740999'; await page.reload();
  await expect(page.getByRole('heading', { name: 'Skill 管理', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '新建 Skill', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveValue('');
  data.state.account.id = firstAccount; await page.reload();
  await expect(page.getByRole('textbox', { name: '显示名称', exact: true })).toHaveValue('甲账号的雨夜草稿');
  expect(data.state.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Skill library: Skill list, long editor and resource recovery stay usable at desktop and narrow widths', async ({ page }, info) => {
  const data = await skillFixture(page); data.state.failList = true;
  await page.goto('/ai_config?section=skills'); await expect(page.getByRole('button', { name: '重新读取 Skill', exact: true })).toBeVisible();
  data.state.failList = false; await page.getByRole('button', { name: '重新读取 Skill', exact: true }).click(); await expect(row(page)).toBeVisible();
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 700 }]) {
    await page.setViewportSize(viewport); await expect(row(page)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`skill-library-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
    data.state.failResource = true; await row(page).getByRole('button', { name: '编辑', exact: true }).click(); await expect(page.getByRole('button', { name: '重新读取资料', exact: true })).toBeVisible();
    data.state.failResource = false; await page.getByRole('button', { name: '重新读取资料', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '资料正文 references/continuity.md', exact: true })).toBeEnabled();
    await page.getByRole('textbox', { name: 'Markdown 主指令', exact: true }).focus(); await page.keyboard.press('Tab');
    expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
    await page.screenshot({ path: info.outputPath(`skill-editor-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '返回列表', exact: true }).click();
  }
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
