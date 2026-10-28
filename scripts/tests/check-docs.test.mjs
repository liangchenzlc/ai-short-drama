import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { collectApiModules, compareApiModuleInventory } from '../docs-api-modules.mjs';

test('route inventory discovers nested HTTP modules and excludes aggregation and cache files', context => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'docs-routes-'));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, 'assistant/conversations'), { recursive: true });
  mkdirSync(path.join(root, '__pycache__'));
  writeFileSync(path.join(root, 'router.py'), 'router.include_router(conversations.router)');
  writeFileSync(path.join(root, 'test.py'), '@router.get("/testing")\ndef testing(): pass');
  writeFileSync(path.join(root, '__pycache__/obsolete.py'), '@router.get("/obsolete")');
  writeFileSync(path.join(root, 'assistant/conversations/management.py'), '@router.get(\n"/global/conversations")\ndef list_all(): pass');
  writeFileSync(path.join(root, 'assistant/conversations/messages.py'), '@router.post("/messages")\ndef send(): pass');
  assert.deepEqual(collectApiModules(root), [
    'assistant/conversations/management', 'assistant/conversations/messages',
  ]);
});

test('route inventory checks the complete section and reports missing and retired paths', () => {
  const lines = ['### 接口分组', '', '| 业务 | 模块 |', '| --- | --- |',
    '| 画布 | `canvas/document/router` |', '', '',
    '| 助手 | `assistant/conversations/management`、`assistant/retired` |',
    '## 数据', '`outside/ignored`'];
  assert.deepEqual(compareApiModuleInventory(lines, [
    'canvas/document/router', 'assistant/conversations/management', 'assistant/conversations/messages',
  ]), [
    { line: 2, message: '接口模块未登记：assistant/conversations/messages' },
    { line: 2, message: '接口模块已不存在：assistant/retired' },
  ]);
});

test('an absent route inventory cannot silently pass', () => {
  assert.deepEqual(compareApiModuleInventory(['# 项目概览'], []), [{ line: 1, message: '缺少接口分组清单' }]);
});
