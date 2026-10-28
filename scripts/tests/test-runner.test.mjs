import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { buildCommands, changedFiles, parseArgs, resultStatus, selectTests, validateMap } from '../test.mjs';

function fixture() {
  return {
    version: 1,
    test_roots: ['backend/tests', 'frontend/tests'],
    auxiliary_roots: [],
    ignored_changes: [{ patterns: ['docs/**', '**/*.md'], reason: '纯文档变更' }],
    features: {
      'standard.assets': { sources: ['backend/src/assets/**', 'frontend/src/assets/**'], depends_on: ['shared.media'] },
      'canvas.video': { sources: ['backend/src/video/**'], depends_on: ['shared.media'] },
      'shared.media': { sources: ['backend/src/storage/**'], depends_on: [] },
    },
    tests: [
      { path: 'backend/tests/assets/test_assets.py', previous_path: 'backend/tests/test_assets.py', features: ['standard.assets'], runner: 'pytest', level: 'fast', requires: [], support: ['backend/tests/conftest.py'] },
      { path: 'frontend/tests/assets/assets.test.mjs', features: ['standard.assets'], runner: 'node', level: 'fast', requires: [], support: [] },
      { path: 'backend/tests/video/test_video.py', features: ['canvas.video'], runner: 'mysql', level: 'infra', requires: ['RUN_CANVAS_RESOURCE_MINIO'], support: [] },
      { path: 'backend/tests/video/test_live.py', features: ['canvas.video'], runner: 'mysql', level: 'live', requires: ['RUN_AGNES_LIVE'], support: [] },
    ],
    auxiliary: [{ path: 'backend/tests/conftest.py', features: ['standard.assets'], kind: 'helper', status: 'active' }],
  };
}

test('按功能只选择该功能的快速测试，文件顺序稳定且去重', () => {
  const result = selectTests(fixture(), { features: ['standard.assets', 'standard.assets'], levels: ['fast'] });
  assert.deepEqual(result.tests.map(row => row.path), ['backend/tests/assets/test_assets.py', 'frontend/tests/assets/assets.test.mjs']);
  assert.ok(result.tests.every(row => result.reasons[row.path].length));
});

test('共享源码修改反向选择消费者，普通业务修改保持功能范围', () => {
  const map = fixture();
  const shared = selectTests(map, { changedPaths: ['backend/src/storage/upload.py'], levels: ['fast', 'infra'] });
  assert.equal(shared.tests.length, 3);
  assert.deepEqual(shared.features, ['canvas.video', 'shared.media', 'standard.assets']);
  const local = selectTests(map, { changedPaths: ['frontend/src/assets/editor.ts'], levels: ['fast', 'infra'] });
  assert.equal(local.tests.length, 2);
});

test('辅助文件变更选择实际引用者，迁移前路径也能定位测试', () => {
  const map = fixture();
  assert.deepEqual(selectTests(map, { changedPaths: ['backend/tests/conftest.py'], levels: ['fast'] }).tests.map(row => row.path), ['backend/tests/assets/test_assets.py']);
  assert.equal(selectTests(map, { changedPaths: ['backend/tests/test_assets.py'], levels: ['fast'] }).tests.length, 1);
});

test('代码未匹配、未知功能、级别为空时失败；纯文档明确返回无需测试', () => {
  assert.throws(() => selectTests(fixture(), { changedPaths: ['backend/src/new_feature.py'], levels: ['fast'] }), /未映射/);
  assert.throws(() => selectTests(fixture(), { features: ['missing'], levels: ['fast'] }), /未知功能/);
  assert.throws(() => selectTests(fixture(), { features: ['canvas.video'], levels: ['fast'] }), /没有/);
  const result = selectTests(fixture(), { changedPaths: ['docs/instruction/按功能执行测试.md'], levels: ['fast'] });
  assert.equal(result.noTestsRequired, true);
});

test('全量快速测试不会因继承真实模型开关而选中 live', () => {
  const result = selectTests(fixture(), { all: true, levels: ['fast'] });
  assert.equal(result.tests.length, 2);
  assert.ok(result.tests.every(row => row.level !== 'live'));
});

test('真实项目映射覆盖组件和跨功能测试，局部改动不扩大全部功能', () => {
  const map = JSON.parse(readFileSync(new URL('../../test-map.json', import.meta.url), 'utf8'));
  const assembly = selectTests(map, { changedPaths: ['frontend/src/features/creation/assembly/workbench/AssemblyWorkbench.tsx'] });
  assert.ok(assembly.tests.some(row => row.path.endsWith('/assembly-editing.test.mjs')));
  assert.equal(assembly.tests.some(row => row.features.includes('canvas.models')), false);
  const writing = selectTests(map, { changedPaths: ['frontend/src/features/creation/writing/editor/ScriptSceneEditor.tsx'] });
  assert.ok(writing.tests.some(row => row.path.endsWith('/writing-session.test.mjs')));
  const projects = selectTests(map, { changedPaths: ['backend/src/short_drama/service/projects/management/project.py'] });
  assert.ok(projects.tests.some(row => row.path.endsWith('/test_project_creation.py')));
  assert.ok(projects.tests.some(row => row.path.endsWith('/project-creation.test.mjs')));
  const catalog = selectTests(map, { features: ['canvas.models'] });
  assert.ok(catalog.tests.some(row => row.path.endsWith('/test_canvas_catalog.py')));
  const vendor = selectTests(map, { changedPaths: ['frontend/canvas/vendor/lobehub-icons/upstream.json'] });
  assert.ok(vendor.tests.some(row => row.path.endsWith('/model-logo-integrity.test.mjs')));
});

test('拆分源码的旧删除路径选择全部新职责，显式退役仍运行所属业务回归', () => {
  const map = fixture();
  map.source_migrations = { 'backend/src/old.py': ['backend/src/assets/editor.py', 'backend/src/storage/upload.py'] };
  const moved = selectTests(map, { changedPaths: ['backend/src/old.py'], levels: ['fast', 'infra'] });
  assert.ok(moved.features.includes('standard.assets'));
  assert.ok(moved.features.includes('canvas.video'));
  map.source_migrations['backend/src/retired.py'] = [];
  map.removed_sources = [{ path: 'backend/src/retired.py', features: ['standard.assets'], reason: '小说专属链已退役' }];
  const retired = selectTests(map, { changedPaths: ['backend/src/retired.py'] });
  assert.equal(retired.tests.length, 2);
  assert.equal(retired.noTestsRequired, false);
});

test('迁移映射拒绝失效目标和没有退役说明的空映射', context => {
  const root = mkdtempSync(join(tmpdir(), 'test-map-migrations-'));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const map = fixture();
  for (const row of [...map.tests, ...map.auxiliary]) {
    mkdirSync(join(root, row.path, '..'), { recursive: true });
    writeFileSync(join(root, row.path), '');
  }
  map.source_migrations = { 'backend/src/old.py': ['backend/src/missing.py'] };
  assert.throws(() => validateMap(map, root), /迁移目标已失效/);
  map.source_migrations['backend/src/old.py'] = [];
  assert.throws(() => validateMap(map, root), /缺少说明/);
});

test('跳过、零用例和仅收集不能记为测试通过', () => {
  assert.equal(resultStatus(0, 10, 0), 'passed');
  assert.equal(resultStatus(0, 10, 1), 'incomplete');
  assert.equal(resultStatus(0, 0, 0), 'incomplete');
  assert.equal(resultStatus(1, 10, 0), 'failed');
  assert.equal(resultStatus(null, 10, 0), 'failed');
});

test('命令使用精确文件和正确 cwd，Python 与 Node 分开执行', () => {
  const map = fixture();
  const selected = selectTests(map, { features: ['standard.assets'], levels: ['fast'] });
  const commands = buildCommands(map, selected.tests, { root: process.cwd(), reportDir: join(process.cwd(), '.runtime', 'unit-report') });
  assert.equal(commands.length, 2);
  const python = commands.find(row => row.runner === 'pytest');
  const node = commands.find(row => row.runner === 'node');
  assert.match(python.cwd.replaceAll('\\', '/'), /\/backend$/);
  assert.ok(python.args.includes('tests/assets/test_assets.py'));
  assert.ok(node.args.includes('tests/assets/assets.test.mjs'));
  assert.ok(commands.every(row => row.args.every(arg => !arg.includes('*'))));
  assert.ok(python.args.includes('--basetemp'));
  assert.ok(python.args.some(arg => arg.startsWith('cache_dir=')));
});

test('Playwright 使用可匹配 Windows 和 POSIX 路径的精确文件过滤器', () => {
  const map = fixture();
  const row = { path: 'frontend/e2e/assets/example.spec.ts', runner: 'playwright' };
  const command = buildCommands(map, [row], { root: process.cwd() })[0];
  const filter = command.args.find(arg => arg.startsWith('^'));
  const expression = new RegExp(filter);
  const expected = join(process.cwd(), row.path);
  assert.ok(expression.test(expected.replaceAll('\\', '/')));
  assert.ok(expression.test(expected.replaceAll('/', '\\')));
  assert.equal(expression.test(expected + '.other'), false);
});

test('清单校验拒绝遗漏、失效路径和越界路径', () => {
  const root = mkdtempSync(join(tmpdir(), 'short-drama-test-map-'));
  try {
    const map = fixture();
    for (const row of [...map.tests, ...map.auxiliary]) {
      mkdirSync(join(root, row.path, '..'), { recursive: true });
      writeFileSync(join(root, row.path), '');
    }
    assert.doesNotThrow(() => validateMap(map, root));
    writeFileSync(join(root, 'backend/tests/assets/test_new.py'), '');
    assert.throws(() => validateMap(map, root), /未登记/);
    rmSync(join(root, 'backend/tests/assets/test_new.py'));
    map.tests[0].path = '../escape.py';
    assert.throws(() => validateMap(map, root), /越界|非法/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('CLI 拒绝隐式全量、非法级别和互相冲突的选择参数', () => {
  assert.throws(() => parseArgs([]), /指定/);
  assert.throws(() => parseArgs(['--feature', 'standard.assets', '--all']), /选择/);
  assert.throws(() => parseArgs(['--feature', 'standard.assets', '--level', 'unknown']), /级别/);
  assert.deepEqual(parseArgs(['--feature', 'standard.assets', '--level', 'fast,browser']).levels, ['fast', 'browser']);
});

test('Git 选择包括已暂存、未暂存、新文件以及重命名两端，支持分支基线', () => {
  const root = mkdtempSync(join(tmpdir(), 'short-drama-test-git-'));
  const git = (...args) => {
    const result = spawnSync('git', args, { cwd: root, encoding: 'utf8', windowsHide: true });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout;
  };
  try {
    git('init', '-q');
    git('config', 'user.name', 'Test Runner');
    git('config', 'user.email', 'runner@example.test');
    for (const name of ['staged.ts', 'working.ts', 'renamed.ts']) writeFileSync(join(root, name), 'initial\n');
    git('add', '.');
    git('commit', '-qm', 'fixture');
    const base = git('rev-parse', 'HEAD').trim();
    writeFileSync(join(root, 'staged.ts'), 'staged\n');
    git('add', 'staged.ts');
    writeFileSync(join(root, 'working.ts'), 'working\n');
    git('mv', 'renamed.ts', 'renamed-new.ts');
    writeFileSync(join(root, '未跟踪.ts'), 'new\n');
    assert.deepEqual(changedFiles(root), ['renamed-new.ts', 'renamed.ts', 'staged.ts', 'working.ts', '未跟踪.ts'].sort());
    git('add', '.');
    git('commit', '-qm', 'branch change');
    assert.equal(changedFiles(root).length, 0);
    assert.equal(changedFiles(root, base).length, 5);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
