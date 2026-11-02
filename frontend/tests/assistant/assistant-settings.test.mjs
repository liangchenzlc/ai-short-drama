import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const path = new URL('../../src/features/assistant/settings/assistant-settings-state.ts', import.meta.url);
const settings = { custom_instructions: null, default_instructions: '默认指令', model_call_limit: 100, output_token_limit: 1000000, subagent_max_concurrency: 3, default_media_limits: { images: null, videos: 0, audio: 2 }, settings_version: '9007199254740993', deployment_limits: { model_call_limit: 2000, output_token_limit: 20000000, subagent_max_concurrency: 3 } };
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
test('successful save updates its baseline while a newer edit and null or zero media values survive', async () => {
  const { settingsState, editSettings, receiveSettings, settingsPatch } = await implementation();
  let state = receiveSettings(settingsState(), settings, 0);
  state = editSettings(state, { custom_instructions: '提交的指令' });
  const patch = settingsPatch(state);
  assert.equal(patch.settings_version, '9007199254740993');
  assert.deepEqual(patch.default_media_limits, { images: null, videos: 0, audio: 2 });
  state = editSettings(state, { custom_instructions: '提交后又输入' });
  state = receiveSettings(state, { ...settings, ...patch, settings_version: '9007199254740994' }, 1, true);
  assert.equal(state.draft.custom_instructions, '提交后又输入');
  state = editSettings(state, { custom_instructions: null });
  assert.equal(settingsPatch(state).custom_instructions, null);
});

test('MCP requests keep creation secret separate from list responses and use server scope options', async () => {
  const token = { id: '9007199254740993', name: '外部工作台', token_hint: 'mcp_…1234', expires_at: null, allowed_ips: [], project_ids: ['9007199254740995'], tool_names: ['workbench__list_projects'], created_at: '2026-10-30' };
  const choices = { projects: [{ id: token.project_ids[0], name: '本人作品' }], tools: [{ name: token.tool_names[0], description: '列出作品', modes: ['standard'], mutating: false }] };
  const calls = [];
  const stub = {
    async get(path) { calls.push(['GET', path]); return { data: path.endsWith('/options') ? choices : [token] }; },
    async post(path, body) { calls.push(['POST', path, body]); return { data: { ...token, secret: 'fixture-secret-once' } }; },
    async delete(path) { calls.push(['DELETE', path]); },
  };
  globalThis.__task11McpHttp = stub;
  try {
    const httpUrl = `data:text/javascript;base64,${Buffer.from('export const http = globalThis.__task11McpHttp;').toString('base64')}`;
    const source = readFileSync(new URL('../../src/api/assistant/settings/mcp-tokens.ts', import.meta.url), 'utf8').replace('@host/api/http', httpUrl);
    const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
    const { mcpTokensApi } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
    const body = { name: token.name, expires_at: null, allowed_ips: [], project_ids: token.project_ids, tool_names: token.tool_names };
    assert.deepEqual(await mcpTokensApi.options(), choices);
    assert.equal((await mcpTokensApi.create(body)).secret, 'fixture-secret-once');
    const listed = await mcpTokensApi.list();
    assert.deepEqual(listed, [token]); assert.equal(Object.hasOwn(listed[0], 'secret'), false);
    await mcpTokensApi.remove(token.id);
    assert.deepEqual(calls, [['GET', '/account/mcp-tokens/options'], ['POST', '/account/mcp-tokens', body], ['GET', '/account/mcp-tokens'], ['DELETE', `/account/mcp-tokens/${token.id}`]]);
  } finally { delete globalThis.__task11McpHttp; }
});
