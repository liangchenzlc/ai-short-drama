import { spawn, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LEVELS = ['fast', 'mysql', 'browser', 'infra', 'live', 'performance', 'runtime'];
const RUNNERS = ['pytest', 'mysql', 'storage', 'node', 'canvas-node', 'playwright', 'canvas-browser', 'performance', 'runtime', 'tooling'];
const output = message => process.stdout.write(message + '\n');
const slash = value => value.replaceAll('\\', '/');
const unique = values => [...new Set(values)].sort();
const testFile = path => /(?:^|\/)test_[^/]+\.py$|\.(?:test|spec)\.(?:mjs|js|ts|tsx)$/.test(path);

function inside(root, path) {
  if (typeof path !== 'string' || !path || isAbsolute(path) || /^[A-Za-z]:/.test(path)
    || path.includes('\\') || path.split('/').some(part => part === '..' || part === '.') || path.includes('*')) {
    throw new Error(`非法或越界路径：${path}`);
  }
  const absolute = resolve(root, path);
  if (relative(root, absolute).startsWith('..')) throw new Error(`越界路径：${path}`);
  return absolute;
}

function walk(root, directory) {
  const absolute = inside(root, directory);
  if (!existsSync(absolute)) return [];
  return readdirSync(absolute, { withFileTypes: true }).flatMap(entry => {
    if (['__pycache__', '.pytest_cache', '.runtime', 'node_modules'].includes(entry.name)) return [];
    const path = directory + '/' + entry.name;
    return entry.isDirectory() ? walk(root, path) : entry.isFile() ? [path] : [];
  });
}

function matches(path, pattern) {
  const tokens = pattern.split('**');
  const expression = tokens.map(token => token.split('*').map(part => part.replace(/[|\\{}()[\]^$+?.]/g, '\\$&')).join('[^/]*')).join('.*');
  return new RegExp('^' + expression + '$').test(path);
}

export function validateMap(map, root = ROOT) {
  if (map.version !== 1 || !map.features || !Array.isArray(map.tests) || !Array.isArray(map.auxiliary)) throw new Error('测试清单格式或版本无效');
  const known = new Set();
  const entries = [...map.tests, ...map.auxiliary];
  for (const row of entries) {
    const absolute = inside(root, row.path);
    if (known.has(row.path)) throw new Error(`重复登记：${row.path}`);
    known.add(row.path);
    if (!existsSync(absolute) || !statSync(absolute).isFile()) throw new Error(`登记路径已失效：${row.path}`);
    if (!row.features?.length || row.features.some(id => !map.features[id])) throw new Error(`功能归属无效：${row.path}`);
  }
  for (const row of map.tests) {
    if (!testFile(row.path) || !RUNNERS.includes(row.runner) || !LEVELS.includes(row.level)) throw new Error(`测试运行方式无效：${row.path}`);
    if (row.level === 'live' && !row.requires?.some(name => name.includes('LIVE'))) throw new Error(`真实供应商测试缺少显式开关：${row.path}`);
    for (const path of row.support ?? []) if (!known.has(path)) throw new Error(`辅助文件未登记：${row.path} → ${path}`);
    if ((row.requires ?? []).some(name => !/^(?:RUN_|TEST_)[A-Z0-9_]+$/.test(name))) throw new Error(`环境开关无效：${row.path}`);
  }
  for (const [id, value] of Object.entries(map.features)) {
    for (const dependency of value.depends_on ?? []) if (!map.features[dependency]) throw new Error(`未知依赖功能：${id} → ${dependency}`);
    for (const path of value.contracts ?? []) if (!map.tests.some(row => row.path === path && row.level === 'fast')) throw new Error(`公共契约路径无效：${id} → ${path}`);
  }
  for (const directory of [...map.test_roots, ...(map.auxiliary_roots ?? [])]) {
    inside(root, directory);
    for (const path of walk(root, directory)) if (!known.has(path)) throw new Error(`文件未登记，请更新 test-map.json：${path}`);
  }
  const rootFiles = map.test_roots.flatMap(path => walk(root, path)).filter(testFile);
  if (map.tests.some(row => !rootFiles.includes(row.path))) throw new Error('测试文件必须位于已声明的 test_roots 内');
  return { tests: map.tests.length, auxiliary: map.auxiliary.length, features: Object.keys(map.features).length };
}

export function selectTests(map, options = {}) {
  const levels = options.levels ?? ['fast'];
  const selected = new Map();
  const reasons = {};
  const features = new Set();
  const ignored = [];
  const unmapped = [];
  const direct = new Set();
  const add = (row, reason) => {
    selected.set(row.path, row);
    reasons[row.path] = unique([...(reasons[row.path] ?? []), reason]);
  };
  const addFeature = (id, reason) => {
    if (!map.features[id]) throw new Error(`未知功能：${id}`);
    features.add(id);
    for (const row of map.tests) if (row.features.includes(id)) add(row, reason);
  };
  for (const id of options.features ?? []) addFeature(id, `指定功能：${id}`);
  if (options.all) for (const id of Object.keys(map.features)) addFeature(id, '显式选择全部功能');
  for (const path of options.changedPaths ?? []) {
    const tests = map.tests.filter(row => row.path === path || row.previous_path === path);
    const auxiliary = map.auxiliary.find(row => row.path === path || row.previous_path === path);
    const retired = [...(map.removed_tests ?? []), ...(map.removed_auxiliary ?? [])].find(row => row.path === path);
    if (tests.length) {
      for (const row of tests) { add(row, `测试文件变更：${path}`); row.features.forEach(id => features.add(id)); }
      for (const row of map.tests) if (row.support?.includes(tests[0].path)) {
        add(row, `引用的测试辅助函数变更：${path}`);
        row.features.forEach(id => features.add(id));
      }
      continue;
    }
    if (auxiliary) {
      const consumers = map.tests.filter(row => row.support?.includes(auxiliary.path));
      for (const row of consumers) { add(row, `夹具或辅助文件变更：${path}`); row.features.forEach(id => features.add(id)); }
      if (!consumers.length) for (const id of auxiliary.features) direct.add(id);
      continue;
    }
    if (retired) { retired.features.forEach(id => direct.add(id)); continue; }
    const owners = Object.entries(map.features).filter(([, value]) => value.sources?.some(pattern => matches(path, pattern)));
    if (owners.length) { owners.forEach(([id]) => direct.add(id)); continue; }
    const skip = (map.ignored_changes ?? []).find(row => row.patterns.some(pattern => matches(path, pattern)));
    if (skip) ignored.push({ path, reason: skip.reason });
    else unmapped.push(path);
  }
  if (unmapped.length) throw new Error(`以下改动未映射到测试，请补充 test-map.json：\n${unmapped.join('\n')}`);
  const impacted = new Set(direct);
  let changed = true;
  while (changed) {
    changed = false;
    for (const [id, value] of Object.entries(map.features)) {
      if (!impacted.has(id) && (value.depends_on ?? []).some(dependency => impacted.has(dependency))) {
        impacted.add(id); changed = true;
      }
    }
  }
  for (const id of impacted) addFeature(id, direct.has(id) ? `源码变更：${id}` : `受依赖功能影响：${id}`);
  const withinScope = row => (!options.runners?.length || options.runners.includes(row.runner))
    && (!options.scope || options.scope === 'all' || (options.scope === 'backend' ? row.path.startsWith('backend/')
      : options.scope === 'canvas' ? row.path.startsWith('frontend/canvas/')
        : options.scope === 'frontend' ? row.path.startsWith('frontend/') && !row.path.startsWith('frontend/canvas/')
          : row.path.startsWith('scripts/')));
  const required = [...selected.values()].filter(row => withinScope(row) && !levels.includes(row.level));
  let tests = [...selected.values()].filter(row => levels.includes(row.level) && withinScope(row));
  if (!tests.length && (selected.size || options.features?.length || options.all || direct.size)) throw new Error(`所选功能在 ${levels.join(',')} 级别没有测试；请查看 --list 或选择其他级别`);
  // 公共契约只补充到已经选中的快速测试，避免用公共用例掩盖功能没有测试。
  if (tests.some(row => row.level === 'fast') && !options.runners?.length && !options.scope) {
    for (const id of features) for (const path of map.features[id].contracts ?? []) {
      const row = map.tests.find(item => item.path === path);
      if (row && !tests.some(item => item.path === path)) { tests.push(row); reasons[path] = [`${id} 的公共契约`]; }
    }
  }
  tests = tests.sort((a, b) => a.path.localeCompare(b.path, 'en'));
  return { tests, features: [...features].sort(), reasons, ignored, notSelected: required.map(row => ({ path: row.path, level: row.level })), noTestsRequired: tests.length === 0 };
}

function git(root, args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8', windowsHide: true, maxBuffer: 16 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`读取 Git 改动失败：${result.error?.message ?? result.stderr.trim()}`);
  return result.stdout;
}

export function changedFiles(root = ROOT, base) {
  const files = [];
  if (base) {
    const ancestor = git(root, ['merge-base', base, 'HEAD']).trim();
    files.push(...git(root, ['diff', '--name-only', '--no-renames', '-z', ancestor, 'HEAD']).split('\0'));
  }
  files.push(...git(root, ['diff', '--name-only', '--no-renames', '-z']).split('\0'));
  files.push(...git(root, ['diff', '--cached', '--name-only', '--no-renames', '-z']).split('\0'));
  files.push(...git(root, ['ls-files', '--others', '--exclude-standard', '--exclude=.workbuddy/**', '-z']).split('\0'));
  return unique(files.filter(Boolean).map(slash));
}

export function parseArgs(args) {
  const options = { features: [], levels: ['fast'], runners: [], files: [], extra: [] };
  const value = (index, flag) => {
    if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`${flag} 缺少参数`);
    return args[index + 1];
  };
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === '--') { options.extra = args.slice(index + 1); break; }
    if (flag === '--feature') options.features.push(...value(index++, flag).split(','));
    else if (flag === '--level') options.levels = value(index++, flag).split(',');
    else if (flag === '--runner') options.runners.push(...value(index++, flag).split(','));
    else if (flag === '--scope') options.scope = value(index++, flag);
    else if (flag === '--base') options.base = value(index++, flag);
    else if (['--all', '--changed', '--dry-run', '--check', '--list', '--json', '--help', '--inventory'].includes(flag)) options[flag.slice(2).replace('-r', 'R')] = true;
    else if (flag.startsWith('-')) throw new Error(`未知参数：${flag}；原生运行器参数放在 -- 后`);
    else options.files.push(flag);
  }
  if (options.levels.some(level => !LEVELS.includes(level))) throw new Error(`未知级别；可选：${LEVELS.join(', ')}`);
  if (options.runners.some(runner => !RUNNERS.includes(runner))) throw new Error(`未知运行器：${options.runners.join(', ')}`);
  if (options.scope && !['backend', 'frontend', 'canvas', 'tooling', 'all'].includes(options.scope)) throw new Error('未知 --scope');
  const selectors = Number(options.features.length > 0) + Number(Boolean(options.all)) + Number(Boolean(options.changed)) + Number(options.files.length > 0);
  if (selectors > 1) throw new Error('--feature、--all、--changed 和文件路径只能选择一种');
  if (!selectors && !options.help && !options.check && !options.list && !options.inventory) throw new Error('请指定 --feature <功能>、--changed 或 --all；使用 --list 查看功能');
  if (options.base && !options.changed) throw new Error('--base 需要同时指定 --changed');
  return options;
}

export function buildCommands(map, tests, { root = ROOT, reportDir = join(ROOT, '.runtime/test-results') } = {}) {
  const groups = new Map();
  for (const row of tests) {
    // 按层分开 pytest 进程，避免同名测试模块及模块级配置互相污染。
    const layer = row.runner === 'pytest' ? row.path.split('/')[2] : '';
    const key = row.runner + ':' + layer;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const python = process.platform === 'win32' ? join(root, 'backend/.venv/Scripts/python.exe') : join(root, 'backend/.venv/bin/python');
  return [...groups.entries()].map(([key, rows], index) => {
    const runner = rows[0].runner;
    const cwd = join(root, ['pytest', 'mysql', 'storage'].includes(runner) ? 'backend'
      : runner.startsWith('canvas-') ? 'frontend/canvas' : runner === 'tooling' ? '' : 'frontend');
    const files = rows.map(row => slash(relative(cwd, join(root, row.path))));
    const report = join(reportDir, `${index}-${runner}.json`);
    let executable = process.execPath;
    let args;
    if (['pytest', 'mysql', 'storage'].includes(runner)) {
      executable = existsSync(python) ? python : 'uv';
      const prefix = existsSync(python) ? [] : ['run', 'python'];
      args = [...prefix, ...(runner === 'mysql' ? ['scripts/run_integration.py'] : ['-m', 'pytest']), ...files, '-q',
        '--basetemp', join(reportDir, `tmp-${index}`), '-o', `cache_dir=${join(reportDir, `cache-${index}`)}`,
        '-p', 'scripts.test_result_plugin'];
    } else if (runner === 'playwright' || runner === 'performance') {
      const filters = files.map(file => '^' + slash(resolve(cwd, file)).split('/').map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[/\\\\]') + '$');
      args = ['node_modules/@playwright/test/cli.js', 'test', ...(runner === 'performance' ? ['--config', 'performance.config.ts'] : []), ...filters, '--reporter=list,json'];
    } else {
      args = [...(runner === 'canvas-node' ? ['--experimental-strip-types'] : []), '--test', '--test-reporter=tap', ...files];
    }
    return { key, runner, cwd, executable, args, files: rows.map(row => row.path), report,
      env: { SHORT_DRAMA_TEST_REPORT: report, PLAYWRIGHT_JSON_OUTPUT_NAME: report } };
  });
}

export function resultStatus(exitCode, passed, skipped) {
  if (exitCode !== 0) return 'failed';
  return skipped || !passed ? 'incomplete' : 'passed';
}

async function execute(command, extra) {
  const started = Date.now();
  return new Promise(resolveResult => {
    let tail = '';
    let settled = false;
    let cancelled = false;
    const child = spawn(command.executable, [...command.args, ...extra], { cwd: command.cwd,
      env: { ...process.env, ...command.env }, windowsHide: true, stdio: ['inherit', 'pipe', 'pipe'] });
    const capture = (stream, target) => stream.on('data', data => {
      target.write(data);
      tail = (tail + data.toString()).slice(-32768);
    });
    capture(child.stdout, process.stdout);
    capture(child.stderr, process.stderr);
    const stop = () => { cancelled = true; child.kill('SIGTERM'); };
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
    const finish = result => {
      if (settled) return;
      settled = true;
      process.removeListener('SIGINT', stop);
      process.removeListener('SIGTERM', stop);
      resolveResult({ runner: command.runner, files: command.files, duration_ms: Date.now() - started, cancelled, ...result });
    };
    child.once('error', error => finish({ exit_code: 1, status: 'failed', error: error.message }));
    child.once('close', code => {
      let skipped = Number([...tail.matchAll(/^# skipped (\d+)/gm)].at(-1)?.[1] ?? 0);
      let passed = Number([...tail.matchAll(/^# pass (\d+)/gm)].at(-1)?.[1] ?? 0);
      try {
        if (existsSync(command.report)) {
          const report = JSON.parse(readFileSync(command.report, 'utf8'));
          skipped = report.skipped ?? report.stats?.skipped ?? skipped;
          passed = report.passed ?? report.stats?.expected ?? passed;
        }
      } catch (error) {
        finish({ exit_code: 1, status: 'failed', error: `读取测试报告失败：${error.message}` });
        return;
      }
      finish({ exit_code: code, passed, skipped, status: resultStatus(code, passed, skipped) });
    });
  });
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArgs(args);
  if (options.help) {
    output('用法：node scripts/test.mjs --feature standard.assets [--level fast,mysql,browser]');
    output('      node scripts/test.mjs --changed [--base <分支或提交>] [--dry-run]');
    output('      node scripts/test.mjs --check | --list | --all --level fast');
    return 0;
  }
  const map = JSON.parse(readFileSync(join(ROOT, 'test-map.json'), 'utf8'));
  const inventory = validateMap(map);
  if (options.check) { output(JSON.stringify({ status: 'valid', ...inventory })); return 0; }
  if (options.inventory) {
    const lines = ['# 测试文件清单', '', '由 `node scripts/test.mjs --inventory` 根据 `test-map.json` 生成。功能、级别、前置条件以清单为准；此表不表示测试已执行。', '',
      `共 ${inventory.tests} 个测试文件、${inventory.auxiliary} 个辅助文件、${inventory.features} 个功能。`, '',
      '| 功能 | 测试文件 | 级别 | 运行器 | 环境开关 |', '| --- | --- | --- | --- | --- |'];
    for (const row of map.tests) lines.push(`| ${row.features.join(', ')} | \`${row.path}\` | ${row.level} | ${row.runner} | ${(row.requires ?? []).join(', ')} |`);
    lines.push('', '## 夹具、辅助文件与验收脚本', '', '| 文件 | 功能 | 类型 | 状态 |', '| --- | --- | --- | --- |');
    for (const row of map.auxiliary) lines.push(`| \`${row.path}\` | ${row.features.join(', ')} | ${row.kind} | ${row.status} |`);
    writeFileSync(join(ROOT, 'docs/instruction/测试文件清单.md'), lines.join('\n') + '\n');
    output('已更新 docs/instruction/测试文件清单.md');
    return 0;
  }
  if (options.list) {
    for (const [id, feature] of Object.entries(map.features).sort(([a], [b]) => a.localeCompare(b))) {
      const rows = map.tests.filter(row => row.features.includes(id));
      const levels = unique(rows.map(row => row.level));
      output(`${id.padEnd(26)} ${String(rows.length).padStart(3)} 个文件  ${levels.join(', ')}  ${feature.description ?? ''}`);
    }
    return 0;
  }
  const paths = options.changed ? changedFiles(ROOT, options.base) : options.files.map(path => slash(relative(ROOT, resolve(process.cwd(), path))));
  const selection = selectTests(map, { ...options, changedPaths: paths });
  const reportDir = join(ROOT, '.runtime/test-results', new Date().toISOString().replaceAll(':', '-') + '-' + randomUUID().slice(0, 8));
  const commands = buildCommands(map, selection.tests, { reportDir });
  const plan = { ...selection, tests: selection.tests.map(row => ({ ...row, reasons: selection.reasons[row.path] })), commands };
  if (options.dryRun || options.json) { output(JSON.stringify(plan, null, 2)); return 0; }
  if (selection.noTestsRequired) { output(JSON.stringify({ status: 'no_tests_required', ignored: selection.ignored })); return 0; }
  const requirements = unique(selection.tests.flatMap(row => row.requires ?? []));
  const missing = requirements.filter(name => process.env[name] !== '1');
  if (missing.length) throw new Error(`选中测试需要显式配置环境开关（不会自动开启）：${missing.join(', ')}；用 --dry-run 查看范围`);
  mkdirSync(reportDir, { recursive: true });
  const results = [];
  output(`选中 ${selection.tests.length} 个测试文件，功能：${selection.features.join(', ')}；未选择其他级别的 ${selection.notSelected.length} 个文件。`);
  for (const command of commands) {
    output(`运行 ${command.runner}：${command.files.length} 个文件，目录 ${slash(relative(ROOT, command.cwd)) || '.'}`);
    const result = await execute(command, options.extra);
    results.push(result);
    if (result.cancelled) break;
  }
  const status = results.some(row => row.status === 'failed') ? 'failed' : results.some(row => row.status === 'incomplete') ? 'incomplete' : 'passed';
  const summary = { status, features: selection.features, levels: options.levels, selected_files: selection.tests.map(row => row.path), ignored: selection.ignored, not_selected: selection.notSelected, results };
  writeFileSync(join(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  output(`结果：${status}；报告：${slash(relative(ROOT, join(reportDir, 'summary.json')))}`);
  return status === 'passed' ? 0 : status === 'incomplete' ? 2 : 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(code => { process.exitCode = code; }).catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
}
