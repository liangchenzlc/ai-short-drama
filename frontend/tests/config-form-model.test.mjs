import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/ai-config/config-form-model.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { createConfigDraft, serializeConfigDraft, parseRuntimeDraft, safeCanvasReturn, trustedBeefAPIPage, beefAPITestOrigin, usesRuntimeModelDiscovery, runtimeModelDiscoveryBody } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const profile = () => ({ version: 1, api_format: 'claude', protocol: 'claude-message', reference_asset_origin: 'https://media.example.com', capability_config: { version: 1, text: { streaming: false, references: { maxImages: 2, maxVideos: 0 } } }, default_options: { temperature: 0, count: 1 }, logical_capability_spec: { version: 1, capability: 'text', inputs: { images: { min: 0, max: 2 } } }, logical_capability_profiles: [{ version: 1, capability: 'text', operations: ['chat'] }], video_capabilities_version: 'fixture-v1', concurrency_limit: 4 });
const existing = () => ({ id: '9007199254740993', name: '模型', provider: '供应商', serviceType: 'text', baseUrl: 'https://provider.example/v1', modelKey: 'model', enabled: true, hasApiKey: true, hasSecretKey: true, headers: [{ name: 'X-Private', has_value: true }], credentialSource: 'manual', runtimeProfile: profile(), rowVersion: '9007199254740994', isDefault: false });

test('高级协议、能力、默认参数与逻辑规范在编辑普通名称时完整保留', () => {
  const draft = createConfigDraft(existing(), 'text');
  draft.name = ' 新名称 ';
  const body = serializeConfigDraft(draft);
  assert.equal(body.name, '新名称');
  assert.deepEqual(body.runtime_profile, profile());
  assert.equal('apikey' in body, false);
  assert.equal('secret_key' in body, false);
  assert.equal('headers' in body, false);
  assert.equal(draft.apiKey, '');
  assert.equal(draft.secretKey, '');
  assert.equal(draft.headers[0].value, '');
});

test('服务端可选运行参数的null还原为空表单，改名可保存且保留已有默认参数', () => {
  const saved = existing();
  saved.runtimeProfile = { version: 1, api_format: 'openai', protocol: 'chat-completion', reference_asset_origin: null, capability_config: null, default_options: { temperature: 0 }, logical_capability_spec: null, logical_capability_profiles: null, video_capabilities_version: null, concurrency_limit: null };
  const draft = createConfigDraft(saved, 'text');
  for (const field of ['referenceAssetOrigin', 'capabilityConfig', 'logicalCapabilitySpec', 'logicalCapabilityProfiles', 'videoCapabilitiesVersion', 'concurrencyLimit']) assert.equal(draft.runtime[field], '');
  draft.name = '修改名称';
  assert.deepEqual(serializeConfigDraft(draft).runtime_profile, { version: 1, api_format: 'openai', protocol: 'chat-completion', default_options: { temperature: 0 } });
  saved.runtimeProfile.default_options = null;
  assert.equal(createConfigDraft(saved, 'text').runtime.defaultOptions, '');
});

test('密钥留空保留，新增覆盖，显式清除独立提交null', () => {
  const draft = createConfigDraft(existing(), 'text');
  draft.apiKey = 'new-key'; draft.secretKey = 'new-secret';
  assert.equal(serializeConfigDraft(draft).apikey, 'new-key');
  assert.equal(serializeConfigDraft(draft).secret_key, 'new-secret');
  draft.clearApiKey = true; draft.clearSecretKey = true;
  assert.equal(serializeConfigDraft(draft).apikey, null);
  assert.equal(serializeConfigDraft(draft).secret_key, null);
});

test('请求头同名空值保留；删除全部显式提交空数组', () => {
  const draft = createConfigDraft(existing(), 'text');
  draft.headersChanged = true;
  assert.deepEqual(serializeConfigDraft(draft).headers, [{ name: 'X-Private', value: '' }]);
  draft.headers.push({ name: 'X-New', value: 'new-private-value', hasValue: false });
  assert.equal(serializeConfigDraft(draft).headers[1].value, 'new-private-value');
  draft.headers = [];
  assert.deepEqual(serializeConfigDraft(draft).headers, []);
});

test('普通配置的主动目录探测保留自定义请求头，删除全部也不退回旧接口', () => {
  const draft = createConfigDraft(null, 'text');
  assert.equal(usesRuntimeModelDiscovery(draft), false);
  draft.baseUrl = ' https://new.example.test/v1 ';
  draft.apiKey = 'fixture-only-key';
  draft.secretKey = 'fixture-only-second-key';
  draft.headers = [{ name: ' X-Private ', value: 'fixture-only-header', hasValue: false }];
  draft.headersChanged = true;
  assert.equal(usesRuntimeModelDiscovery(draft), true);
  assert.deepEqual(runtimeModelDiscoveryBody(draft, null), { baseUrl: 'https://new.example.test/v1', apiFormat: 'openai', apiKey: 'fixture-only-key', headers: [{ name: 'X-Private', value: 'fixture-only-header' }] });
  draft.headers = [];
  assert.equal(usesRuntimeModelDiscovery(draft), true);
  assert.deepEqual(runtimeModelDiscoveryBody(draft, null).headers, []);
});

test('同地址才引用已存密钥和请求头，显式清除不会变成遗漏', () => {
  const saved = existing();
  saved.runtimeProfile = null;
  const draft = createConfigDraft(saved, 'text');
  draft.baseUrl = ' https://provider.example/v1/// ';
  assert.deepEqual(runtimeModelDiscoveryBody(draft, saved), { baseUrl: 'https://provider.example/v1///', apiFormat: 'openai', channelId: `host-${saved.id}`, credentialRef: `host:${saved.id}`, headers: [{ name: 'X-Private', value: '' }] });
  draft.apiKey = 'ignored-when-cleared'; draft.clearApiKey = true;
  assert.equal(runtimeModelDiscoveryBody(draft, saved).apiKey, null);
  draft.headers = []; draft.headersChanged = true;
  assert.deepEqual(runtimeModelDiscoveryBody(draft, saved).headers, []);
});

test('换地址不能借用旧遮罩凭据；填写新请求头或删除后只发送新凭据', () => {
  const saved = existing();
  const draft = createConfigDraft(saved, 'text');
  draft.runtime.enabled = false;
  draft.baseUrl = 'https://different.example.test/v1';
  draft.apiKey = 'fixture-new-address-key';
  assert.throws(() => runtimeModelDiscoveryBody(draft, saved), /服务地址已修改/);
  draft.headers[0].value = 'fixture-new-address-header';
  const body = runtimeModelDiscoveryBody(draft, saved);
  assert.deepEqual(body, { baseUrl: draft.baseUrl, apiFormat: 'openai', apiKey: draft.apiKey, headers: [{ name: 'X-Private', value: 'fixture-new-address-header' }] });
  draft.apiKey = ''; draft.headers = []; draft.headersChanged = true;
  const empty = runtimeModelDiscoveryBody(draft, saved);
  for (const field of ['channelId', 'credentialRef', 'apiKey']) assert.equal(field in empty, false);
  assert.deepEqual(empty.headers, []);
});

test('目录探测先校验请求头，不能带受保护字段或换行发送', () => {
  const draft = createConfigDraft(null, 'text');
  draft.baseUrl = 'https://new.example.test/v1'; draft.apiKey = 'fixture-only-key';
  for (const header of [{ name: 'Authorization', value: 'secret' }, { name: 'X-Private', value: 'a\r\nb' }]) {
    draft.headers = [{ ...header, hasValue: false }];
    assert.throws(() => runtimeModelDiscoveryBody(draft, null), /受保护|换行/);
  }
});

test('高级JSON拒绝无效内容、错误根类型、非有限数字和隐藏认证字段', () => {
  for (const value of ['{', 'null', '[]', 'true', '"text"', '{"n":1e999}', '{"nested":{"api_key":"secret"}}', '{"nested":{"API-KEY":"secret"}}']) {
    const draft = createConfigDraft(existing(), 'text'); draft.runtime.defaultOptions = value;
    assert.throws(() => serializeConfigDraft(draft));
  }
  const draft = createConfigDraft(existing(), 'text');
  draft.runtime.logicalCapabilityProfiles = '[1]';
  assert.throws(() => serializeConfigDraft(draft), /对象数组/);
});

test('请求头拒绝重复、受保护字段、CRLF和超限内容', () => {
  for (const headers of [[{ name: 'Authorization', value: 'secret' }], [{ name: 'Proxy-Authenticate', value: 'secret' }], [{ name: 'X'.repeat(129), value: 'secret' }], [{ name: 'X-A', value: 'a' }, { name: 'x-a', value: 'b' }], [{ name: 'X-A', value: 'a\r\nb' }], [{ name: 'X-A', value: 'a'.repeat(4097) }]]) {
    const draft = createConfigDraft(existing(), 'text'); draft.headers = headers; draft.headersChanged = true;
    assert.throws(() => serializeConfigDraft(draft));
  }
});

test('停用高级配置显式清除；并发与参考源站校验不接受猜测值', () => {
  const draft = createConfigDraft(existing(), 'text');
  draft.runtime.enabled = false;
  assert.equal(serializeConfigDraft(draft).runtime_profile, null);
  draft.runtime.enabled = true;
  for (const value of ['0', '-1', '1.5', '1025', '1e2']) { draft.runtime.concurrencyLimit = value; assert.throws(() => parseRuntimeDraft(draft.runtime)); }
  draft.runtime.concurrencyLimit = '';
  for (const value of ['file:///tmp/a', 'https://user:password@example.com', 'https://media.example/a', 'https://media.example?key=secret']) {
    draft.runtime.referenceAssetOrigin = value; assert.throws(() => parseRuntimeDraft(draft.runtime));
  }
});

test('返回只允许完整画布深链并保留查询和hash', () => {
  const path = '/canvas-app/canvas/source_123?node=abc&asset=%2Ffoo#viewport';
  assert.equal(safeCanvasReturn(path), path);
  for (const value of [null, '', '//evil.example', 'https://evil.example/canvas-app/canvas/a', '/projects', '/canvas-app/settings', '/canvas-app/canvas/a/../../settings', '/canvas-app/canvas/%2e%2e', '/canvas-app/canvas/a\\evil']) assert.equal(safeCanvasReturn(value), null);
});

test('官方授权和钱包地址限制到可信企业origin与原路径', () => {
  assert.equal(trustedBeefAPIPage('https://enterprise.beefapi.com/desktop-auth?code=fixture', 'authorization'), 'https://enterprise.beefapi.com/desktop-auth?code=fixture');
  assert.equal(trustedBeefAPIPage('https://enterprise.beefapi.com/console/topup', 'wallet'), 'https://enterprise.beefapi.com/console/topup');
  for (const value of ['https://evil.example/console/topup', 'https://enterprise.beefapi.com/console/topup?redirect=evil', 'https://user:pass@enterprise.beefapi.com/console/topup', 'https://enterprise.beefapi.com/a/../console/topup']) assert.throws(() => trustedBeefAPIPage(value, 'wallet'));
});

test('企业测试源站只允许显式test模式与纯loopback origin，默认保持官方', () => {
  const origin = 'http://127.0.0.1:4195';
  assert.equal(beefAPITestOrigin('test', origin), origin);
  assert.equal(trustedBeefAPIPage(`${origin}/desktop-auth?user_code=fixture`, 'authorization', { mode: 'test', origin }), `${origin}/desktop-auth?user_code=fixture`);
  assert.equal(trustedBeefAPIPage(`${origin}/console/topup`, 'wallet', { mode: 'test', origin }), `${origin}/console/topup`);
  for (const mode of ['production', 'development', '']) assert.throws(() => trustedBeefAPIPage(`${origin}/desktop-auth`, 'authorization', { mode, origin }));
  for (const value of ['http://127.0.0.1:4195/', 'http://127.0.0.1:4195/path', 'http://user@127.0.0.1:4195', 'http://127.0.0.1:4195?x=1', 'http://127.0.0.1:4195#x', 'http://external.example.test:4195', 'https://127.0.0.1:4195']) assert.throws(() => beefAPITestOrigin('test', value));
  assert.throws(() => trustedBeefAPIPage(`${origin}/desktop-auth`, 'authorization'));
  for (const value of [`${origin}/console/topup?`, `${origin}/console/topup#`, `${origin}/console/topup/other`]) assert.throws(() => trustedBeefAPIPage(value, 'wallet', { mode: 'test', origin }));
});
