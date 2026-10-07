import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';
const time = '2026-10-03T00:00:00Z';
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };
const artifact = (kind = 'novel_proposal', id = '901'): any => ({
  id, project_id: '10', episode_id: '20', kind, status: 'ready', row_version: '1', preview: '雨夜中，她看见了信的主人。',
  source_snapshot: { episode_id: '20', content_version: '1', storyboard_version: '1', episode_row_version: '1', target_kind: 'episode', target_id: '20', target_row_version: null, model_name: '创作协作模型', generated_at: time },
  script_id: kind === 'script_candidate' ? '41' : null, parent_script_id: '40', generation_task_id: null, media_asset_id: null, media_id: null, target_asset_id: null, target_shot_id: null,
  created_by: '1', created_at: time, updated_at: time, applied_by: null, applied_at: null, apply_receipt: null, content_origin: 'snapshot', content: '雨夜中，她看见了信的主人。', patch: null, diff: [],
});
async function artifactFixture(page: Page, enabled = true, schema = true) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const items: any[] = [artifact()]; const calls: { path: string; method: string; body: any }[] = [];
  let needShared = false; let failSource = false; let continueFailures = 0;
  let adoptGate: ReturnType<typeof deferred> | null = null; let adoptStarted: ReturnType<typeof deferred> | null = null;
  const run: any = { id: '801', conversation_id: '301', status: 'waiting_review', phase: 'wait', row_version: '1', mode: 'workflow', model_config_id: '71', model_name: '创作协作模型', error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: ['901'], created_at: time, updated_at: time, finished_at: null };
  await page.route('**/api/v1/agent/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname.slice('/api/v1'.length); const method = req.method(); const body = method === 'POST' ? req.postDataJSON() : null;
    calls.push({ path, method, body }); const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/agent/status') return reply({ enabled, schema_ready: schema });
    if (path === '/agent/models') return reply({ items: [{ id: '71', name: '创作协作模型', model_key: 'fixture', row_version: '1', protocol: 'chat', verified: true, tool_calling: true, tool_result_continuation: true, streaming: 'verified', preferred: true }], preferred_id: '71' });
    const conversation = { id: '301', project_id: '10', episode_id: '20', stage: 'source', subject_type: 'episode', subject_id: '20', task_type: 'writing', scope_version: 1, title: '本人的创作对话', row_version: '1', archived: false, last_run_status: run.status, created_at: time, updated_at: time };
    const assetConversation = { ...conversation, id: '302', stage: 'assets', task_type: 'extraction', last_run_status: null };
    if (path === '/agent/conversations') return method === 'POST' ? reply(conversation, 201) : reply({ items: [conversation], total: 1, offset: 0, limit: 20 });
    if (path === '/agent/conversations/resolve') return reply(body.stage === 'assets' ? assetConversation : conversation);
    if (path === '/agent/conversations/301') return reply(conversation);
    if (path === '/agent/conversations/302') return reply(assetConversation);
    if (path.startsWith('/agent/conversations/302/') && !path.endsWith('/events')) return reply(path.endsWith('/state') ? { conversation_id: '302', cursor: 0, active_run: null, queued_runs: [] } : { items: [], total: 0, offset: 0, limit: 50 });
    if (path.endsWith('/messages')) return reply({ items: [{ id: '501', seq: 1, role: 'user', content: '私密创作讨论', references: [], artifacts: [{ artifact_id: '901' }], created_at: time }], total: 1, offset: 0, limit: 50 });
    if (path.endsWith('/attachments') && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (path.endsWith('/state')) return reply({ conversation_id: '301', cursor: 0, active_run: run, queued_runs: [] });
    if (path.endsWith('/runs')) return reply({ items: [run], total: 1, offset: 0, limit: 1 });
    if (path.endsWith('/events')) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path === '/agent/runs/801/continue') {
      if (continueFailures-- > 0) return reply({ error: { code: 'TEMPORARY' } }, 503);
      Object.assign(run, { status: 'queued', phase: 'model', awaiting_artifact_ids: [] }); return reply(run);
    }
    base.unexpected.push(`${method} ${path}`); return reply({}, 501);
  });
  await page.route(`**/api/v1${root}/agent-artifacts**`, async route => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname.slice('/api/v1'.length); const method = req.method(); const body = method === 'POST' ? req.postDataJSON() : null;
    calls.push({ path, method, body }); const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const id = path.split('/')[6]; const item = items.find(item => item.id === id);
    if (path.endsWith('/agent-artifacts')) {
      const visible = items.filter(item => (!url.searchParams.get('kind') || item.kind === url.searchParams.get('kind')) && (!url.searchParams.get('status') || item.status === url.searchParams.get('status')));
      const offset = Number(url.searchParams.get('offset') || 0); return reply({ items: visible.slice(offset, offset + 20), total: visible.length, offset, limit: 20 });
    }
    if (!item) return reply({ error: { code: 'NOT_FOUND' } }, 404);
    if (method === 'POST') {
      adoptStarted?.resolve(); if (adoptGate) await adoptGate.promise;
      if (failSource) return reply({ error: { code: 'agent_source_changed' } }, 409);
      if (needShared && !body.confirm_shared) return reply({ error: { code: 'shared_asset_confirmation_required', details: { reference_count: 3 } } }, 409);
      if (item.status !== 'applied') {
        Object.assign(item, { status: 'applied', row_version: String(BigInt(item.row_version) + 1n), applied_by: '1', applied_at: time });
        if (item.kind === 'novel_proposal') { base.writing.content_version = String(BigInt(base.writing.content_version) + 1n); base.writing.novel.content = item.content; }
        if (item.kind === 'script_candidate') { base.writing.content_version = String(BigInt(base.writing.content_version) + 1n); Object.assign(base.writing.editing_script, { id: '41', content: item.content, state: 'unconfirmed' }); }
        item.apply_receipt = { artifact_id: item.id, content_version: base.writing.content_version, storyboard_version: '1', episode_row_version: '1', created: 1, reused: 0, already_applied: false, applied_at: time };
      }
    }
    return reply(item);
  });
  return { ...base, items, calls, run, shared: () => { needShared = true; }, stale: () => { failSource = true; }, failContinue: () => { continueFailures = 1; }, gateAdopt: () => { adoptGate = deferred(); adoptStarted = deferred(); return { started: adoptStarted.promise, release: adoptGate.resolve }; } };
}
const openShelf = async (page: Page) => {
  await page.locator('.agent-message-results').getByRole('button', { name: '核对候选', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '核对创作候选' })).toBeVisible();
};
const adoptCalls = (state: Awaited<ReturnType<typeof artifactFixture>>) => state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/adopt'));

test('private candidates open from their author conversation and adoption stays explicit', async ({ page }) => {
  const state = await artifactFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await expect(page.getByText('采用会替换本集小说正文，原候选和来源快照保留。')).toBeVisible();
  await page.getByRole('button', { name: '确认采用', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '确认采用', exact: true }).click();
  await expect(page.getByText('已采用到作品。')).toBeVisible();
  expect(adoptCalls(state)[0].body).toMatchObject({ row_version: '1', content_version: '1', storyboard_version: '1' });
  expect(state.calls.filter(call => call.path.includes('/continue'))).toEqual([]);
  await page.getByRole('dialog', { name: '核对创作候选' }).getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '本集小说正文' })).toHaveValue('雨夜中，她看见了信的主人。');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('legacy script text is identified without presenting it as the original generated draft', async ({ page }) => {
  const state = await artifactFixture(page);
  Object.assign(state.items[0], { kind: 'script_candidate', script_id: '41', content_origin: 'current_script_legacy', content: '已经人工修改的当前剧本。' });
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  await expect(page.getByRole('dialog', { name: '核对创作候选' })).toContainText('这份历史候选展示的是当前剧本正文，原始生成稿无法确认。');
  await expect(page.getByLabel('候选正文', { exact: true })).toHaveText('已经人工修改的当前剧本。');
  await expect(page.getByLabel('原始生成候选正文', { exact: true })).toHaveCount(0);
  expect(adoptCalls(state)).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('large artifact versions are adopted and continued as exact decimal strings', async ({ page }) => {
  const state = await artifactFixture(page);
  state.items[0].row_version = '9007199254740993';
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('button', { name: '核对候选 1', exact: true }).click();
  await expect(page.getByLabel('原始生成候选正文', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '采用并继续', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '采用并继续', exact: true }).click();
  await expect.poll(() => state.calls.filter(call => call.path.includes('/continue')).length).toBe(1);
  expect(adoptCalls(state)[0].body.row_version).toBe('9007199254740993');
  expect(state.calls.find(call => call.path.includes('/continue'))!.body.artifact_row_version).toBe('9007199254740994');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('unmigrated deployments never request the artifact API', async ({ page }) => {
  const state = await artifactFixture(page, false, false); await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '本集小说正文' })).toBeVisible();
  await expect(page.getByRole('button', { name: /创作候选/ })).toHaveCount(0);
  expect(state.calls.filter(call => call.path.includes('/agent-artifacts'))).toEqual([]);
});

test('prompt writing and materials expose no creation candidate shelf', async ({ page }) => {
  const state = await artifactFixture(page, false);
  for (const stage of ['source', 'assets']) {
    await page.goto(`${root}/${stage}`);
    await expect(page.getByRole('button', { name: /创作候选/ })).toHaveCount(0);
    await expect(page.getByRole('region', { name: '我的创作候选', exact: true })).toHaveCount(0);
  }
  expect(state.calls.filter(call => call.path.includes('/agent-artifacts'))).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const delayedRead of ['versions', 'target'] as const) test(`leaving the episode during ${delayedRead} read prevents a new adoption request`, async ({ page }) => {
  const state = await artifactFixture(page);
  Object.assign(state.items[0], { kind: 'asset_patch', content: null, target_asset_id: '501', patch: { description: '浅色风衣' } });
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  const started = deferred(); const gate = deferred();
  const endpoint = delayedRead === 'target' ? '**/api/v1/assets/501' : `**/api/v1${root}/shots?*`;
  await page.route(endpoint, async route => { started.resolve(); await gate.promise; await route.fallback(); });
  await page.getByRole('button', { name: '确认采用', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '确认采用', exact: true }).click();
  await started.promise;
  await page.evaluate(() => { window.history.pushState(null, '', '/projects/10'); window.dispatchEvent(new PopStateEvent('popstate')); });
  await expect(page).toHaveURL(url => url.pathname === '/projects/10');
  const response = page.waitForResponse(result => result.request().method() === 'GET' && result.url().includes(delayedRead === 'target' ? '/assets/501' : '/shots?'));
  gate.resolve(); await response;
  expect(adoptCalls(state)).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('private candidate access loss invalidates an earlier detail response', async ({ page }) => {
  const state = await artifactFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await expect(page.locator('.agent-message-results')).toBeVisible();
  const started = deferred(); const gate = deferred();
  await page.route(`**/api/v1${root}/agent-artifacts/901?*`, async route => { started.resolve(); await gate.promise; await route.fallback(); });
  await page.locator('.agent-message-results').getByRole('button', { name: '核对候选', exact: true }).click(); await started.promise;
  await page.route(`**/api/v1${root}/agent-artifacts/901?*`, route => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'FORBIDDEN' } }) }));
  await page.evaluate(() => window.dispatchEvent(new Event('agent-artifacts-updated')));
  await expect(page.getByRole('dialog', { name: '核对创作候选' })).toHaveCount(0);
  const response = page.waitForResponse(result => new URL(result.url()).pathname.endsWith('/agent-artifacts/901'));
  gate.resolve(); await response;
  await expect(page.getByRole('dialog', { name: '核对创作候选' })).toHaveCount(0);
  await expect(page.locator('.agent-artifact-detail')).toHaveCount(0);
  expect(adoptCalls(state)).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a late adoption receipt cannot restore candidate access or continue the private run', async ({ page }) => {
  const state = await artifactFixture(page, true); const gate = state.gateAdopt();
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('button', { name: '核对候选 1', exact: true }).click();
  await page.getByRole('button', { name: '采用并继续', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '采用并继续', exact: true }).click(); await gate.started;
  await page.route(`**/api/v1${root}/agent-artifacts/901?*`, route => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'FORBIDDEN' } }) }));
  await page.evaluate(() => window.dispatchEvent(new Event('agent-artifacts-updated')));
  await expect(page.getByRole('dialog', { name: '核对创作候选' })).toHaveCount(0);
  const response = page.waitForResponse(result => result.request().method() === 'POST' && new URL(result.url()).pathname.endsWith('/adopt'));
  gate.release(); await response;
  await expect(page.getByRole('dialog', { name: '核对创作候选' })).toHaveCount(0);
  await expect(page.locator('.agent-artifact-rows')).toHaveCount(0);
  expect(state.calls.filter(call => call.path.includes('/continue'))).toEqual([]);
  expect(adoptCalls(state)).toHaveLength(1); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('leaving an Agent extraction review during draft save prevents adoption', async ({ page }) => {
  const state = await artifactFixture(page); Object.assign(state.items[0], { kind: 'extraction_candidate', content: null, generation_task_id: '88001' });
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page); await page.getByRole('button', { name: '打开素材审核', exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets'));
  await expect(page.getByRole('button', { name: '加入本集素材库（1）', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  await page.getByRole('textbox', { name: '素材描述', exact: true }).fill('穿着浅色风衣，手握旧信。');
  await expect(page.getByRole('button', { name: '保存候选', exact: true })).toBeVisible();
  const started = deferred(); const gate = deferred();
  await page.route(`**/api/v1${root}/asset-extraction-results/88001`, async route => { if (route.request().method() === 'PATCH') { started.resolve(); await gate.promise; } await route.fallback(); });
  await page.getByRole('button', { name: '加入本集素材库（1）', exact: true }).click(); await started.promise;
  // Trigger the real route handler while the modal is open, as a browser navigation would.
  await page.getByRole('button', { name: '返回项目详情', exact: true }).evaluate(button => (button as HTMLButtonElement).click());
  await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname === '/projects/10');
  const response = page.waitForResponse(result => result.request().method() === 'PATCH' && result.url().endsWith('/asset-extraction-results/88001'));
  gate.resolve(); await response;
  expect(state.calls.filter(call => call.path === '/agent/conversations/resolve' && call.method === 'POST')).toEqual([]);
  expect(state.calls.filter(call => call.path === '/agent/conversations' && call.method === 'GET').length).toBeLessThan(6);
  expect(adoptCalls(state)).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a failed explicit continuation keeps adoption and only retries the specified run', async ({ page }) => {
  const state = await artifactFixture(page, true); state.failContinue();
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('button', { name: '核对候选 1', exact: true }).click();
  await page.getByRole('button', { name: '采用并继续', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '采用并继续', exact: true }).click();
  await expect(page.getByText(/候选已采用，流程尚未确认继续/)).toBeVisible();
  await expect(page.getByRole('dialog', { name: '核对创作候选' }).getByText('已采用', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认采用', exact: true })).toHaveCount(0);
  expect(adoptCalls(state)).toHaveLength(1);
  await page.getByRole('button', { name: '继续当前对话', exact: true }).click();
  await expect(page.getByText('候选已采用，当前对话已继续。')).toBeVisible();
  expect(adoptCalls(state)).toHaveLength(1);
  expect(state.calls.filter(call => call.path.includes('/continue')).map(call => call.body)).toEqual([{ artifact_id: '901', artifact_row_version: '2' }, { artifact_id: '901', artifact_row_version: '2' }]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('patch comparison and shared impact confirmation preserve the original version tokens', async ({ page }, info) => {
  const state = await artifactFixture(page); state.shared();
  Object.assign(state.items[0], { kind: 'asset_patch', content: null, target_asset_id: '501', patch: { description: '穿着浅色风衣。' }, diff: [{ field: 'description', before: '黑发，穿着深色风衣。', after: '穿着浅色风衣。' }], source_snapshot: { ...state.items[0].source_snapshot, target_kind: 'asset', target_id: '501', target_row_version: '1' } });
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  await expect(page.getByRole('region', { name: '候选修改比较' })).toBeVisible();
  await expect(page.getByText('当前来源', { exact: true })).toBeVisible(); await expect(page.getByText('建议修改', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('artifact-diff-1440.png') });
  await page.setViewportSize({ width: 390, height: 900 }); await page.screenshot({ path: info.outputPath('artifact-diff-390.png') });
  await page.getByRole('button', { name: '确认采用', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '确认采用', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认共享影响' })).toContainText('3 处引用');
  await page.getByRole('button', { name: '确认共享修改', exact: true }).click();
  await expect(page.getByText('已采用到作品。')).toBeVisible();
  expect(adoptCalls(state)).toHaveLength(2); expect(adoptCalls(state)[1].body).toEqual({ ...adoptCalls(state)[0].body, confirm_shared: true });
  expect(adoptCalls(state)[0].body.target_row_version).toBe('1');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('stale source and failed save keep candidates without overwriting the work', async ({ page }) => {
  const state = await artifactFixture(page); state.writing.content_version = '2';
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  await expect(page.getByText(/正文已变化，这份候选基于旧版本/)).toBeVisible();
  await expect(page.getByRole('button', { name: '确认采用', exact: true })).toBeDisabled(); expect(adoptCalls(state)).toEqual([]);
  await page.getByRole('dialog', { name: '核对创作候选' }).getByRole('button', { name: '关闭', exact: true }).click();
  state.items[0].source_snapshot.content_version = '2';
  await page.getByRole('textbox', { name: '本集小说正文' }).fill('尚未保存的正文');
  await page.route(`**/api/v1${root}/novel`, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'SAVE_UNAVAILABLE' } }) }));
  await page.locator('.agent-message-results').getByRole('button', { name: '核对候选', exact: true }).click();
  await page.getByRole('button', { name: '确认采用', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用候选' }).getByRole('button', { name: '确认采用', exact: true }).click();
  await expect(page.getByText('当前作品尚未保存成功，本次未采用。请处理保存提示后重试。')).toBeVisible(); expect(adoptCalls(state)).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('storyboard adoption reuses preview and sends the complete native review', async ({ page }) => {
  const state = await artifactFixture(page); Object.assign(state.items[0], { kind: 'storyboard_candidate', content: null, generation_task_id: '8001' });
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  await expect(page.getByRole('heading', { name: '分镜预览（共 55 镜）' })).toBeVisible();
  await page.getByRole('button', { name: '追加到现有分镜', exact: true }).click(); await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page.getByText('已采用到作品。')).toBeVisible();
  expect(adoptCalls(state)[0].body.native_review).toEqual({ mode: 'append', content_version: '1', storyboard_version: '1', confirm_replace: false });
  expect(state.requests.filter(call => call.path.endsWith('/apply') && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('extraction adoption keeps the existing item-by-item review and supplies native choices', async ({ page }) => {
  const state = await artifactFixture(page); Object.assign(state.items[0], { kind: 'extraction_candidate', content: null, generation_task_id: '88001' });
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page); await page.getByRole('button', { name: '打开素材审核', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '素材提取结果' })).toBeVisible();
  await expect(page.getByRole('button', { name: '加入本集素材库（1）', exact: true })).toBeEnabled();
  expect(adoptCalls(state)).toEqual([]);
  await page.getByRole('button', { name: '加入本集素材库（1）', exact: true }).click();
  await expect(page.getByText('已加入本集素材库：新增 1 项，复用 0 项。')).toBeVisible();
  expect(adoptCalls(state)[0].body.native_review).toMatchObject({ result_version: '1', content_version: '1', items: [{ candidate_id: 'a'.repeat(32), action: 'create' }] });
  expect(state.calls.filter(call => call.path.includes('/continue'))).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const kind of ['image_candidate', 'video_candidate']) test(`${kind} preview and adoption stay explicit with shared confirmation`, async ({ page }) => {
  const state = await artifactFixture(page);
  Object.assign(state.items[0], { kind, content: null, generation_task_id: '92001', media_asset_id: '7001', media_id: '9901', target_shot_id: '101', source_snapshot: { ...state.items[0].source_snapshot, target_kind: 'shot', target_id: '101', target_row_version: '1' } });
  const isVideo = kind === 'video_candidate';
  const poster = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#242b3e"/></svg>').toString('base64');
  await page.route('**/api/v1/media-library/items/7001', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ asset_id: '7001', generation_id: '92001', media_id: '9901', media_type: isVideo ? 'video' : 'image', name: '雨夜车站', url: isVideo ? 'data:video/mp4;base64,AAAA' : poster, row_version: '1' }) }));
  await page.route('**/api/v1/ai/generations/92001', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generation_id: '92001', status: 'succeeded', source: { scene: isVideo ? 'shot_video' : 'shot_image', shot_id: '101', layout: 'four' }, parameters: { aspect: '16:9', resolution: isVideo ? '720p' : '2K', ...(isVideo ? { duration_ms: 5000 } : {}), secret: 'DO_NOT_RENDER' }, result: { assets: [], text: null, partial: false } }) }));
  await page.goto(`${root}/source?mode=agent&conversation=301`); await openShelf(page);
  await expect(page.getByText(isVideo ? '清晰度 720p · 画幅 16:9 · 时长 5 秒' : '清晰度 2K · 画幅 16:9 · 布局 四宫格')).toBeVisible();
  await expect(page.getByText('DO_NOT_RENDER')).toHaveCount(0); expect(adoptCalls(state)).toEqual([]);
  if (isVideo) await expect(page.locator('.agent-artifact-media video')).toHaveAttribute('controls', '');
  await page.getByRole('button', { name: '确认采用媒体', exact: true }).click();
  await page.getByRole('dialog', { name: '确认采用媒体' }).getByRole('button', { name: '确认采用', exact: true }).click();
  await expect(page.getByText('已采用到作品。')).toBeVisible();
  expect(adoptCalls(state)[0].body).toMatchObject({ target_row_version: '1', confirm_shared: true }); expect(adoptCalls(state)[0].body.native_review).toBeUndefined();
  expect(state.calls.filter(call => call.path.includes('/continue'))).toEqual([]);
  expect(state.requests.filter(call => /^\/ai\/generations\/(image|video|text)$/.test(call.path) && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
