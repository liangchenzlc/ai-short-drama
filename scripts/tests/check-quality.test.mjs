import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { checkQuality } from '../check-quality.mjs';

function fixture(t, files, structural = {}) {
  const root = mkdtempSync(path.join(tmpdir(), 'short-drama-quality-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const baseline = 'scripts/quality-baseline.json';
  for (const [rel, content] of Object.entries({
    [baseline]: JSON.stringify({ version: 2, structural, sizes: {} }), ...files,
  })) {
    mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    writeFileSync(path.join(root, rel), content);
  }
  return { root, baseline: path.join(root, baseline) };
}

const run = (root, mode = 'check') => checkQuality({ root, mode, output() {} });

test('Python absolute, relative, package and function-local imports form real dependency cycles', t => {
  const { root } = fixture(t, {
    'backend/src/short_drama/__init__.py': '',
    'backend/src/short_drama/service/__init__.py': '',
    'backend/src/short_drama/service/a.py': 'from short_drama.service import b\n',
    'backend/src/short_drama/service/b.py': 'def load():\n    from . import a\n    return a\n',
  });
  const result = run(root);
  assert.equal(result.status, 1);
  assert.match(result.structural[0].detail, /service\/a.py.*service\/b.py/);
});

test('Relative and aliased Python imports cannot bypass layer rules', t => {
  const { root } = fixture(t, {
    'backend/src/short_drama/api/__init__.py': '',
    'backend/src/short_drama/api/routes.py': 'def handler():\n    from ..dao import records as data\n    return data\n',
    'backend/src/short_drama/dao/records.py': '',
  });
  assert.ok(run(root).structural.some(item => item.rule === 'api → dao' && item.line === 2));
});

test('TypeScript detects generic and alias any without matching string contents', t => {
  const { root } = fixture(t, {
    'frontend/src/value.ts': 'type Box = Promise<any>; type Unsafe = any; const label = "any"; // any\n',
  });
  assert.equal(run(root).bans.filter(item => item.rule === 'TypeScript any').length, 2);
});

test('TypeScript dynamic imports and re-exports participate in the graph', t => {
  const { root } = fixture(t, {
    'frontend/src/a.ts': 'export { value } from "./b.js";\n',
    'frontend/src/b.ts': 'export const value = () => import("./a");\n',
  });
  assert.equal(run(root).status, 1);
  assert.match(run(root).structural[0].detail, /a.ts.*b.ts/);
});

test('@host aliases enforce shared and type boundaries including type-only imports', t => {
  const { root } = fixture(t, {
    'frontend/tsconfig.json': JSON.stringify({ compilerOptions: {
      moduleResolution: 'bundler', baseUrl: '.', paths: { '@host/*': ['src/*'] },
    } }),
    'frontend/src/features/assistant/model.ts': 'export interface Model { id: string }\n',
    'frontend/src/api/assistant/requests.ts': 'export const load = () => true;\n',
    'frontend/src/types/assistant/model.ts': 'import type { Model } from "@host/features/assistant/model"; export type Alias = Model;\n',
    'frontend/src/shared/ui/widget.ts': 'export { load } from "@host/api/assistant/requests";\n',
  });
  const result = run(root);
  assert.equal(result.status, 1);
  assert.ok(result.structural.some(item => item.file === 'frontend/src/types/assistant/model.ts'));
  assert.ok(result.structural.some(item => item.file === 'frontend/src/shared/ui/widget.ts'));
});

test('@host type-only aliases cannot hide a dependency cycle', t => {
  const { root } = fixture(t, {
    'frontend/tsconfig.json': JSON.stringify({ compilerOptions: {
      moduleResolution: 'bundler', baseUrl: '.', paths: { '@host/*': ['src/*'] },
    } }),
    'frontend/src/types/assistant/a.ts': 'import type { B } from "@host/types/assistant/b"; export interface A { value: B }\n',
    'frontend/src/types/assistant/b.ts': 'import type { A } from "@host/types/assistant/a"; export interface B { value: A }\n',
  });
  assert.ok(run(root).structural.some(item => item.rule === '循环依赖（前端）'));
});

test('--update rejects new structural keys and increased counts without changing the baseline', t => {
  const rel = 'backend/src/short_drama/api/routes.py';
  const key = `api → domain::${rel}`;
  for (const structural of [{}, { [key]: 1 }]) {
    const { root, baseline } = fixture(t, {
      [rel]: 'from short_drama.domain import A\nfrom short_drama.domain import B\n',
      'backend/src/short_drama/domain/__init__.py': '',
    }, structural);
    const before = readFileSync(baseline, 'utf8');
    assert.equal(run(root, 'update').status, 1);
    assert.equal(readFileSync(baseline, 'utf8'), before);
  }
});

test('--update permits reduced debt and rejects bans or cycles even if listed in the baseline', t => {
  const rel = 'backend/src/short_drama/api/routes.py';
  const key = `api → domain::${rel}`;
  const reduced = fixture(t, {
    [rel]: 'from short_drama.domain import A\n',
    'backend/src/short_drama/domain/__init__.py': '',
  }, { [key]: 2 });
  assert.equal(run(reduced.root, 'update').status, 0);
  assert.deepEqual(JSON.parse(readFileSync(reduced.baseline)).structural, { [key]: 1 });
  const banned = fixture(t, { 'frontend/src/a.ts': 'type Unsafe = any;\n' });
  const before = readFileSync(banned.baseline, 'utf8');
  assert.equal(run(banned.root, 'update').status, 1);
  assert.equal(readFileSync(banned.baseline, 'utf8'), before);
  const cycle = fixture(t, {
    'backend/src/short_drama/a.py': 'import short_drama.b\n',
    'backend/src/short_drama/b.py': 'import short_drama.a\n',
  }, { '循环依赖（后端）::backend/src/short_drama/a.py': 1 });
  assert.equal(run(cycle.root, 'update').status, 1);
});

test('Python AST metrics count multiline async methods and every parameter kind', t => {
  const rel = 'backend/src/short_drama/service/large.py';
  const lines = [
    'class Example:',
    '    async def execute(',
    '        self, a, /,',
    '        b, c, d, e, f, g, h,',
    '        *args, marker="a,b", **kwargs,',
    '    ):',
    ...Array.from({ length: 101 }, (_, i) => `        value = ${i}`),
    '        return value',
  ];
  const { root, baseline } = fixture(t, { [rel]: `${lines.join('\n')}\n` });
  assert.equal(run(root, 'update').status, 0);
  const metrics = JSON.parse(readFileSync(baseline)).sizes[rel];
  assert.equal(metrics.maxFn, lines.length - 1);
  assert.equal(metrics.maxClass, lines.length);
  assert.equal(metrics.params, 12);
});

test('Source syntax errors fail closed before the baseline can be updated', t => {
  for (const [rel, source, message] of [
    ['backend/src/short_drama/broken.py', 'def broken(:\n', /Python 依赖解析失败/],
    ['frontend/src/broken.ts', 'const broken = ;\n', /TypeScript 解析失败/],
  ]) {
    const { root, baseline } = fixture(t, { [rel]: source });
    const before = readFileSync(baseline, 'utf8');
    assert.throws(() => run(root, 'update'), message);
    assert.equal(readFileSync(baseline, 'utf8'), before);
  }
});

test('Console property, indexed and global access or aliases cannot bypass the ban', t => {
  const { root } = fixture(t, {
    'frontend/src/value.ts': [
      'console.log("x");',
      'console["warn"]("x");',
      'globalThis.console.error("x");',
      'window["console"]["info"]("x");',
      'const { log } = console;',
      'const sink = (console);',
      'const label = "console.log"; const data = { console: "label" };',
    ].join('\n'),
  });
  assert.equal(run(root).bans.filter(item => item.rule === '前端 console').length, 6);
});

test('Axios subpath imports are only permitted in the shared HTTP client', t => {
  const { root } = fixture(t, {
    'frontend/src/api/http.ts': 'import axios from "axios/unsafe/axios.js";\n',
    'frontend/src/value.ts': 'const client = import("axios/unsafe/axios.js");\n',
  });
  assert.deepEqual(run(root).bans.filter(item => item.rule === 'axios 越界').map(item => item.file), ['frontend/src/value.ts']);
});

test('Python imports include new package initializers without inventing ancestor cycles', t => {
  for (const statement of [
    'import short_drama.service.child.worker',
    'from short_drama.service.child import worker',
    'from short_drama.service.child.worker import value',
  ]) {
    const { root } = fixture(t, {
      'backend/src/short_drama/__init__.py': '',
      'backend/src/short_drama/service/__init__.py': '',
      'backend/src/short_drama/service/a.py': `${statement}\n`,
      'backend/src/short_drama/service/child/__init__.py': 'import short_drama.service.a\n',
      'backend/src/short_drama/service/child/worker.py': 'value = 1\n',
    });
    assert.ok(run(root).structural.some(item => item.rule === '循环依赖（后端）' && /service\/a.py.*child\/__init__.py/.test(item.detail)));
  }
  const { root } = fixture(t, {
    'backend/src/short_drama/__init__.py': '',
    'backend/src/short_drama/service/__init__.py': 'from . import a\n',
    'backend/src/short_drama/service/a.py': 'import short_drama.service.helper\n',
    'backend/src/short_drama/service/helper.py': 'value = 1\n',
  });
  assert.equal(run(root).status, 0);
});

test('--report includes absolute bans while remaining read-only and returning success', t => {
  const { root, baseline } = fixture(t, { 'frontend/src/value.ts': 'type Unsafe = any;\n' });
  const before = readFileSync(baseline, 'utf8');
  const output = [];
  assert.equal(checkQuality({ root, mode: 'report', output: line => output.push(line) }).status, 0);
  assert.match(output.join('\n'), /TypeScript any/);
  assert.match(output.join('\n'), /绝对禁令 1 处/);
  assert.equal(readFileSync(baseline, 'utf8'), before);
});
