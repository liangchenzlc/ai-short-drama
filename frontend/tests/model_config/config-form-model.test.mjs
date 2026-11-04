import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/ai-config/editor/config-form-model.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { createConfigDraft, serializeConfigDraft, safeCanvasReturn } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const existing = () => ({ id: '9007199254740993', name: '模型', provider: '供应商', serviceType: 'text', baseUrl: 'https://provider.example/v1', modelKey: 'model', enabled: true, hasApiKey: true, hasSecretKey: true, headers: [{ name: 'X-Private', has_value: true }], credentialSource: 'manual', interfaceType: 'claude-messages', runtimeProfile: { version: 1, api_format: 'claude', protocol: 'claude', default_options: { temperature: 0 } }, rowVersion: '9007199254740994', isDefault: false });

test('普通表单只提交接口身份，隐藏运行参数与已存密钥由后端保留', () => {
  const draft = createConfigDraft(existing(), 'text');
  draft.name = ' 新名称 ';
  const body = serializeConfigDraft(draft);
  assert.equal(body.name, '新名称');
  assert.equal(body.interface_type, 'claude-messages');
  for (const field of ['runtime_profile', 'apikey', 'secret_key', 'headers']) assert.equal(field in body, false);
  assert.equal(draft.apiKey, '');
  assert.equal(draft.secretKey, '');
});

test('新模型默认自动识别，显示名称留空时采用模型标识', () => {
  const draft = createConfigDraft(null, 'image');
  draft.modelKey = ' gpt-image-1.5 ';
  const body = serializeConfigDraft(draft);
  assert.equal(body.interface_type, 'auto');
  assert.equal(body.name, 'gpt-image-1.5');
});

test('密钥留空保留，直接输入覆盖，删除提交null', () => {
  const draft = createConfigDraft(existing(), 'text');
  draft.apiKey = 'new-key'; draft.secretKey = 'new-secret';
  assert.equal(serializeConfigDraft(draft).apikey, 'new-key');
  assert.equal(serializeConfigDraft(draft).secret_key, 'new-secret');
  draft.clearApiKey = true; draft.clearSecretKey = true;
  assert.equal(serializeConfigDraft(draft).apikey, null);
  assert.equal(serializeConfigDraft(draft).secret_key, null);
});

test('返回只允许完整画布深链并保留查询和hash', () => {
  const path = '/canvas-app/canvas/source_123?node=abc&asset=%2Ffoo#viewport';
  assert.equal(safeCanvasReturn(path), path);
  for (const value of [null, '', '//evil.example', 'https://evil.example/canvas-app/canvas/a', '/projects', '/canvas-app/settings', '/canvas-app/canvas/a/../../settings', '/canvas-app/canvas/%2e%2e', '/canvas-app/canvas/a\\evil']) assert.equal(safeCanvasReturn(value), null);
});
