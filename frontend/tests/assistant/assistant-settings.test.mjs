import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const path = new URL('../../src/features/assistant/settings/assistant-settings-state.ts', import.meta.url);
const settings = { custom_instructions: null, default_instructions: '默认指令', workbench_enabled: true, media_generation_enabled: true, long_term_memory_enabled: true, settings_version: '9007199254740993' };
async function implementation() {
  assert.ok(existsSync(path), 'settings editor state must be implemented');
  const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
test('refresh and older save responses keep newer unsaved instructions and exact string versions', async () => {
  const { settingsState, editSettings, receiveSettings } = await implementation();
  let state = receiveSettings(settingsState(), settings, 0);
  state = editSettings(state, { custom_instructions: '尚未保存的指令' });
  state = receiveSettings(state, { ...settings, settings_version: '9007199254740994' }, 0);
  assert.equal(state.draft.custom_instructions, '尚未保存的指令');
  assert.equal(state.saved.settings_version, '9007199254740994');
  state = receiveSettings(state, settings, 1, true);
  assert.equal(state.saved.settings_version, '9007199254740994');
  assert.equal(state.draft.custom_instructions, '尚未保存的指令');
});
test('partial save keeps newer input and merges untouched server fields', async () => {
  const { settingsState, editSettings, receiveSettings, settingsPatch } = await implementation();
  let state = receiveSettings(settingsState(), settings, 0);
  state = editSettings(state, { custom_instructions: '提交的指令' });
  const patch = settingsPatch(state);
  assert.equal(patch.settings_version, '9007199254740993');
  assert.deepEqual(patch, { custom_instructions: '提交的指令', settings_version: settings.settings_version });
  state = editSettings(state, { custom_instructions: '提交后又输入' });
  state = receiveSettings(state, { ...settings, ...patch, workbench_enabled: false, settings_version: '9007199254740994' }, 1, true, patch);
  assert.equal(state.draft.custom_instructions, '提交后又输入');
  assert.equal(state.draft.workbench_enabled, false);
  state = editSettings(state, { custom_instructions: null });
  assert.equal(settingsPatch(state).custom_instructions, null);
});

test('only concurrent edits of the same field need conflict resolution', async () => {
  const { settingsState, editSettings, receiveSettings, settingsConflicts } = await implementation();
  let state = receiveSettings(settingsState(), settings, 0);
  state = editSettings(state, { custom_instructions: '我的指令' });
  assert.deepEqual(settingsConflicts(state, { ...settings, workbench_enabled: false }), []);
  assert.deepEqual(settingsConflicts(state, { ...settings, custom_instructions: '另一窗口指令' }), ['custom_instructions']);
});

test('MCP uses capability inputs, a stable creation key and precise recovery without exposing list secrets', async () => {
  const token = { id: '9007199254740993', name: '外部工作台', token_hint: 'mcp_…1234', create_key: 'original-create', expires_at: null, allowed_ips: [], tool_names: ['workbench__list_projects'], created_at: '2026-10-30', deleted_at: null };
  const choices = { connection_url: 'https://studio.example/mcp/', transport: 'streamable-http', capability_groups: { read: token.tool_names, workbench: [], media: [] }, tools: [{ name: token.tool_names[0], description: '列出作品', modes: ['standard'], mutating: false }] };
  const calls = [];
  const stub = {
    async get(path, config) { calls.push(['GET', path, config]); return { data: path.endsWith('/options') ? choices : [token] }; },
    async post(path, body, config) { calls.push(['POST', path, body, config]); return { data: { ...token, secret: 'fixture-secret-once' } }; },
    async delete(path) { calls.push(['DELETE', path]); },
  };
  globalThis.__task11McpHttp = stub;
  try {
    const httpUrl = `data:text/javascript;base64,${Buffer.from('export const http = globalThis.__task11McpHttp;').toString('base64')}`;
    const source = readFileSync(new URL('../../src/api/assistant/settings/mcp-tokens.ts', import.meta.url), 'utf8').replace('@host/api/http', httpUrl);
    const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
    const { mcpTokensApi } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
    const body = { name: token.name, workbench_enabled: false, media_generation_enabled: false, allowed_ips: [] };
    assert.deepEqual(await mcpTokensApi.options(), choices);
    assert.equal((await mcpTokensApi.create(body, 'original-create')).secret, 'fixture-secret-once');
    const listed = await mcpTokensApi.list();
    assert.deepEqual(listed, [token]); assert.equal(Object.hasOwn(listed[0], 'secret'), false);
    assert.deepEqual(await mcpTokensApi.list(undefined, 'original-create'), [token]);
    await mcpTokensApi.remove(token.id);
    assert.deepEqual(calls, [
      ['GET', '/account/mcp-tokens/options', { signal: undefined }],
      ['POST', '/account/mcp-tokens', body, { headers: { 'Idempotency-Key': 'original-create' } }],
      ['GET', '/account/mcp-tokens', { signal: undefined, params: undefined }],
      ['GET', '/account/mcp-tokens', { signal: undefined, params: { create_key: 'original-create' } }],
      ['DELETE', `/account/mcp-tokens/${token.id}`],
    ]);
  } finally { delete globalThis.__task11McpHttp; }
});
